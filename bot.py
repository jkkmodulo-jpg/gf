"""
bot.py — Main entry point. v6.0
MAHORAGA VERSION: Adaptive Learning & Feedback Loop.
"""
import asyncio
import re
import logging
from telethon import TelegramClient, events

import trader
import tracker
import scanner as sc
import adaptation
from reddit_monitor import RedditMonitor
from config import (
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    YOUR_TELEGRAM_ID,
    CHANNELS,
    TRADE_AMOUNT_SOL,
    AUTO_SELL_MULTIPLIER,
    STOP_LOSS_PERCENT,
    WHITELIST_KEYWORDS,
    BLACKLIST_KEYWORDS,
    KNOWN_PROGRAMS,
    LAMPORTS_PER_SOL,
)

# ── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot")

# ── CA EXTRACTOR ─────────────────────────────────────────────────────────────
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
reddit_task: asyncio.Task = None
reddit_monitor = RedditMonitor()

# ── TELEGRAM CLIENT ───────────────────────────────────────────────────────────
client = TelegramClient("callbot_session", TELEGRAM_API_ID, TELEGRAM_API_HASH)

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

# ── POSITION MONITOR ─────────────────────────────────────────────────────────
async def monitor_position(ca: str, sol_spent: float, tokens_received: int):
    if AUTO_SELL_MULTIPLIER <= 0:
        return

    target_sol = sol_spent * AUTO_SELL_MULTIPLIER
    stop_loss_sol = sol_spent * (1 - STOP_LOSS_PERCENT / 100)

    while True:
        await asyncio.sleep(15)
        pos = tracker.get_position(ca)
        if not pos or pos["status"] != "open":
            break

        current_sol = trader.get_token_value_in_sol(ca, tokens_received)
        if current_sol is None:
            continue

        current_x = current_sol / sol_spent
        should_sell = False
        reason = ""
        
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
                
                # MAHORAGA: SPIN THE WHEEL AFTER SELL
                if trade_record:
                    adaptation.spin_the_wheel(trade_record)
                    await notify(f"🟢 AUTO SELL — {reason}\nPnL: {trade_record['pnl_sol']:.4f} SOL\nMAHORAGA ADAPTING...")
            break

# ── BUY ORCHESTRATOR ─────────────────────────────────────────────────────────
async def execute_buy(ca: str, source: str = "manual", captured_state: dict = None):
    if ca in bought_this_session:
        return
    bought_this_session.add(ca)

    result = trader.buy_token(ca)
    if result["success"]:
        tokens = result["out_amount"]
        tracker.record_buy(ca, TRADE_AMOUNT_SOL, tokens, result["signature"], captured_state)
        await notify(f"🟢 BUY EXECUTED\nToken: {short(ca)}\nSource: {source}")
        if AUTO_SELL_MULTIPLIER > 0 and tokens > 0:
            asyncio.create_task(monitor_position(ca, TRADE_AMOUNT_SOL, tokens))
    else:
        bought_this_session.discard(ca)

# ── SIGNAL HANDLERS ──────────────────────────────────────────────────────────
async def on_scan_buy_signal(token_mint: str, pool_data: dict, captured_state: dict):
    await execute_buy(token_mint, source=f"scanner ({pool_data.get('name', 'unknown')})", captured_state=captured_state)

async def on_reddit_signal(ca: str, sentiment_score: float, source: str):
    sc.social_sentiment_cache[ca] = sentiment_score

# ── LISTENERS ────────────────────────────────────────────────────────────────
@client.on(events.NewMessage(chats=CHANNELS))
async def on_channel_message(event):
    text = event.message.message or getattr(event.message, "caption", "") or ""
    if not text or not passes_filters(text):
        return
    ca = extract_ca(text)
    if ca:
        await execute_buy(ca, source="channel_call")

@client.on(events.NewMessage(from_users=[YOUR_TELEGRAM_ID], pattern=r'^/'))
async def on_command(event):
    global scanner_task, reddit_task
    text = event.raw_text.strip()
    parts = text.split()
    cmd = parts[0].lower()

    if cmd == "/scan" and len(parts) > 1 and parts[1].lower() == "on":
        if not scanner_task or scanner_task.done():
            scanner_task = asyncio.create_task(sc.run_scanner(on_scan_buy_signal, notify))
            reddit_task = asyncio.create_task(reddit_monitor.run(on_reddit_signal))
            await event.reply("🚀 MAHORAGA ADAPTATION STARTED")
        else:
            await event.reply("Bot is already running.")

    elif cmd == "/scan" and len(parts) > 1 and parts[1].lower() == "off":
        sc.stop_scanner()
        reddit_monitor.stop()
        await event.reply("🔴 Bot STOPPED")

    elif cmd == "/status":
        stats = tracker.get_pnl_summary()
        adapt = adaptation.load_adaptation()
        msg = (f"📊 Status:\nTrades: {stats['total_closed']}\nWin Rate: {stats['win_rate']}%\n"
               f"Total PnL: {stats['total_pnl_sol']} SOL\n"
               f"Mahoraga Defense: +{adapt['market_defense_level']}")
        await event.reply(msg)

async def main():
    await client.start()
    log.info("Mahoraga Bot v6.0 online.")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
