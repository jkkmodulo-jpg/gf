"""
bot.py — Mahoraga Bot v7.0
Real-time multi-source trading. No subscription system. No Reddit. Pure execution.
"""
import asyncio
import re
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession

import trader
import tracker
import scanner as sc
import adaptation
from pumpfun_listener import PumpFunListener
from model_validator import get_health_report
from config import (
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    YOUR_TELEGRAM_ID,
    TELEGRAM_SESSION_STRING,
    CHANNELS,
    TRADE_AMOUNT_SOL,
    AUTO_SELL_MULTIPLIER,
    STOP_LOSS_PERCENT,
    WHITELIST_KEYWORDS,
    BLACKLIST_KEYWORDS,
    KNOWN_PROGRAMS,
    LAMPORTS_PER_SOL,
    RPC_URL,
    RPC_WS_URL,
)

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot")

# ── CA EXTRACTOR ──────────────────────────────────────────────────────────────
SOLANA_CA_PATTERN = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')

def extract_ca(message: str):
    matches = SOLANA_CA_PATTERN.findall(message)
    valid = [m for m in matches if m not in KNOWN_PROGRAMS]
    return valid[0] if valid else None

# ── KEYWORD FILTERS ───────────────────────────────────────────────────────────
def passes_filters(message: str) -> bool:
    msg_lower = message.lower()
    if BLACKLIST_KEYWORDS:
        for kw in BLACKLIST_KEYWORDS:
            if kw in msg_lower:
                return False
    return True

# ── SESSION STATE ─────────────────────────────────────────────────────────────
bought_this_session: set[str] = set()
scanner_task: asyncio.Task = None
pumpfun_task: asyncio.Task = None
pumpfun_listener = PumpFunListener(ws_url=RPC_WS_URL, rpc_url=RPC_URL)

# ── TELEGRAM CLIENT ───────────────────────────────────────────────────────────
if not TELEGRAM_SESSION_STRING:
    raise RuntimeError(
        "TELEGRAM_SESSION_STRING is not set. "
        "Run generate_session.py locally, then add it as Railway env var."
    )

client = TelegramClient(StringSession(TELEGRAM_SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)

# ── HELPERS ───────────────────────────────────────────────────────────────────
def short(ca: str) -> str:
    return f"{ca[:6]}...{ca[-4:]}"

async def notify(text: str):
    if not YOUR_TELEGRAM_ID:
        return
    try:
        await client.send_message(YOUR_TELEGRAM_ID, text, parse_mode=None)
    except Exception as e:
        log.error(f"Notification failed: {e}")

# ── POSITION MONITOR ──────────────────────────────────────────────────────────
async def monitor_position(ca: str, sol_spent: float, tokens_received: int):
    if AUTO_SELL_MULTIPLIER <= 0:
        return

    target_sol    = sol_spent * AUTO_SELL_MULTIPLIER
    stop_loss_sol = sol_spent * (1 - STOP_LOSS_PERCENT / 100)

    while True:
        await asyncio.sleep(15)
        pos = tracker.get_position(ca)
        if not pos or pos["status"] != "open":
            break

        current_sol = trader.get_token_value_in_sol(ca, tokens_received)
        if current_sol is None:
            continue

        current_x    = current_sol / sol_spent
        should_sell  = False
        reason       = ""

        if current_sol >= target_sol:
            should_sell = True
            reason = f"TARGET HIT {current_x:.2f}x"
        elif current_sol <= stop_loss_sol:
            should_sell = True
            reason = f"STOP LOSS -{STOP_LOSS_PERCENT}%"

        if should_sell:
            result = trader.sell_token(ca, tokens_received)
            if result["success"]:
                sol_back = result["out_amount"] / LAMPORTS_PER_SOL
                trade_record = tracker.record_sell(ca, sol_back, result["signature"])
                if trade_record:
                    adaptation.spin_the_wheel(trade_record)
                    pnl   = trade_record["pnl_sol"]
                    emoji = "🟢" if pnl > 0 else "🔴"
                    await notify(
                        f"{emoji} AUTO SELL — {reason}\n"
                        f"Token: {short(ca)}\n"
                        f"PnL: {pnl:+.4f} SOL ({current_x:.2f}x)\n"
                        f"Tx: {result['explorer']}\n"
                        f"☸️ MAHORAGA ADAPTING..."
                    )
            break

# ── BUY ORCHESTRATOR ──────────────────────────────────────────────────────────
async def execute_buy(ca: str, source: str = "manual", captured_state: dict = None, skip_session_check: bool = False):
    if not skip_session_check and ca in bought_this_session:
        return
    bought_this_session.add(ca)

    result = trader.buy_token(ca)
    if result["success"]:
        tokens = result["out_amount"]
        tracker.record_buy(ca, TRADE_AMOUNT_SOL, tokens, result["signature"], captured_state)
        await notify(
            f"☸️ MAHORAGA BUY\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Token: {short(ca)}\n"
            f"Source: {source}\n"
            f"Amount: {TRADE_AMOUNT_SOL} SOL\n"
            f"Tx: {result['explorer']}"
        )
        if AUTO_SELL_MULTIPLIER > 0 and tokens > 0:
            asyncio.create_task(monitor_position(ca, TRADE_AMOUNT_SOL, tokens))
    else:
        bought_this_session.discard(ca)
        await notify(f"❌ BUY FAILED: {short(ca)}\nReason: {result.get('error', 'unknown')}")

# ── SIGNAL HANDLERS ───────────────────────────────────────────────────────────
async def on_scan_buy_signal(token_mint: str, pool_data: dict, captured_state: dict):
    await execute_buy(
        token_mint,
        source=f"scanner ({pool_data.get('name', 'unknown')})",
        captured_state=captured_state,
    )

async def on_pumpfun_new_token(token_mint: str):
    """Real-time Pump.fun launch — token is seconds old when this fires."""
    if token_mint in bought_this_session:
        return

    pool_data = {
        "token_mint":      token_mint,
        "pool_address":    token_mint,
        "name":            f"PF:{short(token_mint)}",
        "price_usd":       0.0,
        "liquidity_usd":   0.0,
        "volume_1h":       0.0,
        "price_change_1h": 0.0,
        "age_minutes":     0.1,
        "dex_url":         f"https://pump.fun/{token_mint}",
        "source":          "pumpfun_realtime",
    }

    result = sc.score_token(pool_data)

    await notify(
        f"🎯 PUMP.FUN LAUNCH\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Token: {short(token_mint)}\n"
        f"Score: {result['score']}/{result['min_required']} "
        f"{'✅ BUYING' if result['will_trade'] else '❌ SKIPPING'}\n"
        f"Chart: {pool_data['dex_url']}"
    )

    if result["will_trade"]:
        await execute_buy(token_mint, source="pumpfun_realtime", captured_state=result["captured_state"])

# ── CHANNEL LISTENER ──────────────────────────────────────────────────────────
@client.on(events.NewMessage(chats=CHANNELS))
async def on_channel_message(event):
    """Channel calls — now scored before executing. No more blind buys."""
    text = event.message.message or getattr(event.message, "caption", "") or ""
    if not text or not passes_filters(text):
        return
    ca = extract_ca(text)
    if not ca or ca in bought_this_session:
        return

    pool_data = {
        "token_mint":      ca,
        "pool_address":    ca,
        "name":            f"CH:{short(ca)}",
        "price_usd":       0.0,
        "liquidity_usd":   5000.0,
        "volume_1h":       0.0,
        "price_change_1h": 0.0,
        "age_minutes":     5.0,
        "dex_url":         f"https://dexscreener.com/solana/{ca}",
        "source":          "channel_call",
    }
    result = sc.score_token(pool_data)

    if result["will_trade"]:
        await execute_buy(ca, source="channel_call", captured_state=result["captured_state"])
    else:
        await notify(
            f"📡 CHANNEL CALL — BLOCKED\n"
            f"Token: {short(ca)}\n"
            f"Score: {result['score']}/{result['min_required']}\n"
            f"Mahoraga rejected it."
        )

# ── COMMAND HANDLER ───────────────────────────────────────────────────────────
@client.on(events.NewMessage(from_users=[YOUR_TELEGRAM_ID], pattern=r'^/'))
async def on_command(event):
    global scanner_task, pumpfun_task

    # Only respond in private — never fires in groups
    if not event.is_private:
        return

    text  = event.raw_text.strip()
    parts = text.split()
    cmd   = parts[0].lower()

    if cmd == "/scan" and len(parts) > 1 and parts[1].lower() == "on":
        if not scanner_task or scanner_task.done():
            scanner_task  = asyncio.create_task(sc.run_scanner(on_scan_buy_signal, notify))
            pumpfun_task  = asyncio.create_task(pumpfun_listener.run(on_pumpfun_new_token, notify))
            await event.reply(
                "☸️ MAHORAGA ONLINE\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "GeckoTerminal: ACTIVE\n"
                "Dexscreener:   ACTIVE\n"
                "Pump.fun WS:   ACTIVE"
            )
        else:
            await event.reply("Already running.")

    elif cmd == "/scan" and len(parts) > 1 and parts[1].lower() == "off":
        sc.stop_scanner()
        pumpfun_listener.stop()
        await event.reply("🔴 MAHORAGA OFFLINE")

    elif cmd == "/status":
        stats   = tracker.get_pnl_summary()
        adapt   = adaptation.load_adaptation()
        health  = get_health_report()
        defense = adapt.get("market_defense_level", 0)
        penalties = adapt.get("dynamic_penalties", {})
        p_str = "\n".join([f"  {k}: -{v}" for k, v in penalties.items() if v > 0]) or "  None active"
        await event.reply(
            f"📊 MAHORAGA STATUS\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Trades: {stats['total_closed']} ({stats['wins']}W/{stats['losses']}L)\n"
            f"Win Rate: {stats['win_rate']}%\n"
            f"Total PnL: {stats['total_pnl_sol']:+.4f} SOL\n"
            f"Open: {stats['open_count']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Defense Level: +{defense}\n"
            f"Effective Min Score: {55 + defense}/100\n"
            f"Penalties:\n{p_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"30d Win Rate: {health['win_rate_30d']}\n"
            f"Overfit Resets: {health['total_resets']}"
        )

    elif cmd == "/positions":
        data     = tracker._load()
        open_pos = {ca: p for ca, p in data["positions"].items() if p["status"] == "open"}
        if not open_pos:
            await event.reply("No open positions.")
        else:
            lines = ["📂 OPEN POSITIONS\n━━━━━━━━━━━━━━━━━━━━"]
            for ca, p in open_pos.items():
                current = trader.get_token_value_in_sol(ca, p["tokens_received"])
                if current:
                    x = current / p["sol_spent"]
                    lines.append(f"{short(ca)}: {x:.2f}x | {current:.4f} SOL")
                else:
                    lines.append(f"{short(ca)}: {p['sol_spent']} SOL in | price unavailable")
            await event.reply("\n".join(lines))

    elif cmd == "/buy" and len(parts) > 1:
        ca = parts[1].strip()
        if not (32 <= len(ca) <= 44):
            await event.reply("Invalid contract address.")
        else:
            await event.reply(f"⏳ Buying {short(ca)}...")
            await execute_buy(ca, source="manual", skip_session_check=True)

    elif cmd == "/sell" and len(parts) > 1:
        ca  = parts[1].strip()
        pos = tracker.get_position(ca)
        if not pos or pos["status"] != "open":
            await event.reply(f"No open position for {short(ca)}")
        else:
            await event.reply(f"⏳ Selling {short(ca)}...")
            result = trader.sell_token(ca, pos["tokens_received"])
            if result["success"]:
                sol_back     = result["out_amount"] / LAMPORTS_PER_SOL
                trade_record = tracker.record_sell(ca, sol_back, result["signature"])
                if trade_record:
                    adaptation.spin_the_wheel(trade_record)
                pnl = sol_back - pos["sol_spent"]
                await event.reply(
                    f"{'🟢' if pnl > 0 else '🔴'} SOLD {short(ca)}\n"
                    f"PnL: {pnl:+.4f} SOL\n"
                    f"Tx: {result['explorer']}"
                )
            else:
                await event.reply(f"❌ Sell failed: {result.get('error', 'unknown')}")

    elif cmd == "/reset":
        adaptation.save_adaptation(adaptation.DEFAULT_ADAPTATION)
        await event.reply(
            "☸️ MAHORAGA RESET\n"
            "Defense: 0\n"
            "Penalties: cleared\n"
            "Wheel starts fresh."
        )

    elif cmd == "/debug":
        adapt        = adaptation.load_adaptation()
        defense      = adapt.get("market_defense_level", 0)
        scanner_alive = scanner_task and not scanner_task.done()
        pumpfun_alive = pumpfun_task and not pumpfun_task.done()
        await event.reply(
            f"🔍 DEBUG\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Scanner: {'RUNNING' if scanner_alive else 'STOPPED'}\n"
            f"PumpFun WS: {'RUNNING' if pumpfun_alive else 'STOPPED'}\n"
            f"Tokens analyzed: {len(sc.analyzed_tokens)}\n"
            f"Bought this session: {len(bought_this_session)}\n"
            f"Min score to buy: {55 + defense}/100\n"
            f"RPC: {RPC_URL[:50]}"
        )

    elif cmd == "/start":
        await event.reply(
            "☸️ MAHORAGA BOT v7.0\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "/scan on    — Start all sources\n"
            "/scan off   — Stop everything\n"
            "/buy <CA>   — Manual buy\n"
            "/sell <CA>  — Manual sell\n"
            "/positions  — Open positions\n"
            "/status     — Full stats\n"
            "/debug      — Scanner health\n"
            "/reset      — Clear adaptation\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Sources: GeckoTerminal + Dexscreener + Pump.fun WS"
        )

async def main():
    await client.start()
    log.info("Mahoraga Bot v7.0 online.")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
