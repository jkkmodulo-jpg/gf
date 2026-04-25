"""
scanner.py — Advanced Autonomous Token Discovery & Scoring Engine v6.0
MAHORAGA VERSION: Now with Dynamic Adaptation & Learned Penalties.
"""
import asyncio
import logging
import time
import requests
from typing import Optional, Callable
from config import (
    RPC_URL,
    LAMPORTS_PER_SOL,
    TRADE_AMOUNT_SOL,
)
from sentiment import get_sentiment_score
import adaptation

log = logging.getLogger("scanner")

# ── THRESHOLDS ────────────────────────────────────────────────────────────────
MIN_SCORE_TO_BUY      = 70       # Base score required
MIN_LIQUIDITY_USD     = 5_000    
MIN_TOKEN_AGE_MIN     = 2        
MAX_TOKEN_AGE_MIN     = 120      
MAX_ALREADY_PUMPED    = 300      
SCAN_INTERVAL_SEC     = 45       
MOMENTUM_VOL_THRESHOLD = 1.5     

# ── SESSION STATE ─────────────────────────────────────────────────────────────
analyzed_tokens: set[str] = set()   
social_sentiment_cache: dict[str, float] = {} 
scanner_running: bool = False

# ── SCORE WEIGHTS (Total: 100) ────────────────────────────────────────────────
WEIGHTS = {
    "mint_revoked":    20,   
    "freeze_revoked":  15,   
    "lp_burned":       15,   
    "no_whale":        10,   
    "distributed":     10,   
    "momentum":        15,   
    "vol_liq_ok":      5,    
    "social_hype":     10,   
}

# ── DISCOVERY ─────────────────────────────────────────────────────────────────

def fetch_new_solana_pools() -> list[dict]:
    try:
        resp = requests.get(
            "https://api.geckoterminal.com/api/v2/networks/solana/new_pools",
            params={"page": 1},
            headers={"Accept": "application/json"},
            timeout=15
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        log.error(f"GeckoTerminal fetch failed: {e}")
        return []

def fetch_trending_solana_pools() -> list[dict]:
    try:
        resp = requests.get(
            "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools",
            params={"page": 1, "duration": "1h"},
            headers={"Accept": "application/json"},
            timeout=15
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        log.error(f"GeckoTerminal trending fetch failed: {e}")
        return []

def parse_pool(pool: dict) -> Optional[dict]:
    try:
        attrs = pool.get("attributes", {})
        rels  = pool.get("relationships", {})
        base_token = rels.get("base_token", {}).get("data", {})
        token_id   = base_token.get("id", "")
        if not token_id.startswith("solana_"):
            return None
        token_mint = token_id.replace("solana_", "")
        
        created_at  = attrs.get("pool_created_at", "")
        created_ts  = 0
        if created_at:
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                created_ts = dt.timestamp()
            except:
                pass
        age_minutes = (time.time() - created_ts) / 60 if created_ts else 9999
        
        return {
            "token_mint":       token_mint,
            "pool_address":     attrs.get("address", ""),
            "name":             attrs.get("name", "Unknown"),
            "price_usd":        float(attrs.get("base_token_price_usd", 0) or 0),
            "liquidity_usd":    float(attrs.get("reserve_in_usd", 0) or 0),
            "volume_1h":        float((attrs.get("volume_usd") or {}).get("h1", 0) or 0),
            "price_change_1h":  float((attrs.get("price_change_percentage") or {}).get("h1", 0) or 0),
            "age_minutes":      round(age_minutes, 1),
            "dex_url":          f"https://dexscreener.com/solana/{token_mint}",
        }
    except Exception as e:
        log.error(f"Pool parse error: {e}")
        return None

# ── ON-CHAIN CHECKS ───────────────────────────────────────────────────────────

def check_mint_account(token_mint: str) -> dict:
    try:
        resp = requests.post(
            RPC_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [token_mint, {"encoding": "jsonParsed"}]
            },
            timeout=10
        )
        data = resp.json()
        value = data.get("result", {}).get("value")
        if not value:
            return {"mint_revoked": False, "freeze_revoked": False, "total_supply": 0}
        
        info = value.get("data", {}).get("parsed", {}).get("info", {})
        supply = int(info.get("supply", 0))
        return {
            "mint_revoked":    info.get("mintAuthority") is None,
            "freeze_revoked":  info.get("freezeAuthority") is None,
            "total_supply":    supply,
        }
    except Exception as e:
        log.error(f"Mint check failed: {e}")
        return {"mint_revoked": False, "freeze_revoked": False, "total_supply": 0}

def check_holder_distribution(token_mint: str, total_supply: int) -> dict:
    try:
        resp = requests.post(
            RPC_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenLargestAccounts",
                "params": [token_mint]
            },
            timeout=10
        )
        accounts = resp.json().get("result", {}).get("value", [])
        if not accounts or total_supply == 0:
            return {"no_whale": False, "distributed": False, "top_1_pct": 100, "top_10_pct": 100}
        
        top_1_amount = int(accounts[0].get("amount", 0))
        top_10_amount = sum(int(a.get("amount", 0)) for a in accounts[:10])
        top_1_pct = (top_1_amount / total_supply) * 100
        top_10_pct = (top_10_amount / total_supply) * 100
        
        return {
            "no_whale":    top_1_pct < 10,
            "distributed": top_10_pct < 50,
            "top_1_pct":   round(top_1_pct, 1),
            "top_10_pct":  round(top_10_pct, 1),
        }
    except Exception as e:
        log.error(f"Holder check failed: {e}")
        return {"no_whale": False, "distributed": False, "top_1_pct": 100, "top_10_pct": 100}

# ── SCORING ENGINE ────────────────────────────────────────────────────────────

def score_token(pool_data: dict) -> dict:
    token_mint = pool_data["token_mint"]
    score = 0
    flags = []
    warnings = []
    captured_state = {}

    # 1. Safety: Mint/Freeze Authority
    mint_data = check_mint_account(token_mint)
    captured_state["mint_revoked"] = mint_data["mint_revoked"]
    captured_state["freeze_revoked"] = mint_data["freeze_revoked"]
    
    if mint_data["mint_revoked"]:
        score += WEIGHTS["mint_revoked"]
        flags.append("Contract: Renounced")
    else:
        warnings.append("DANGER: Mint Authority ACTIVE")

    if mint_data["freeze_revoked"]:
        score += WEIGHTS["freeze_revoked"]
        flags.append("Contract: No Freeze")
    else:
        warnings.append("DANGER: Freeze Authority ACTIVE")

    # 2. Safety: Holder Distribution
    holders = check_holder_distribution(token_mint, mint_data["total_supply"])
    captured_state["top_holder_pct"] = holders["top_1_pct"]
    
    if holders["no_whale"]:
        score += WEIGHTS["no_whale"]
        flags.append(f"Holders: Top {holders['top_1_pct']}% (Safe)")
    else:
        warnings.append(f"WHALE: Top {holders['top_1_pct']}%")

    if holders["distributed"]:
        score += WEIGHTS["distributed"]
        flags.append(f"Holders: Distributed")
    else:
        warnings.append(f"CONCENTRATED: Top 10 own {holders['top_10_pct']}%")

    # 3. Momentum: Volume & Price Action
    vol = pool_data.get("volume_1h", 0)
    liq = pool_data.get("liquidity_usd", 0)
    vl_ratio = vol / liq if liq > 0 else 0
    captured_state["vol_liq_ratio"] = vl_ratio
    captured_state["liquidity_usd"] = liq
    
    if vl_ratio >= MOMENTUM_VOL_THRESHOLD:
        score += WEIGHTS["momentum"]
        flags.append(f"Momentum: Volume Spike")
    elif 0.1 <= vl_ratio < MOMENTUM_VOL_THRESHOLD:
        score += 5
        flags.append(f"Momentum: Healthy")

    # 4. Liquidity & LP Safety
    if liq >= MIN_LIQUIDITY_USD:
        score += WEIGHTS["vol_liq_ok"]
    
    if pool_data.get("age_minutes", 0) > 10:
        score += WEIGHTS["lp_burned"]
        flags.append("LP: Likely Locked")

    # 5. Social Sentiment (Reddit)
    social_score = social_sentiment_cache.get(token_mint, 0)
    if social_score > 0.2:
        score += WEIGHTS["social_hype"]
        flags.append(f"Social: Bullish")
    elif social_score < -0.2:
        score -= 10

    # 6. MAHORAGA ADAPTATION (Dynamic Adjustments)
    adaptation_penalty = adaptation.get_dynamic_score_adjustment(pool_data, captured_state)
    score += adaptation_penalty
    if adaptation_penalty < 0:
        warnings.append(f"MAHORAGA: Learned Penalty ({adaptation_penalty})")

    market_defense = adaptation.get_market_defense_bonus()
    final_min_score = MIN_SCORE_TO_BUY + market_defense
    
    if market_defense > 0:
        flags.append(f"MAHORAGA: Defense Level +{market_defense}")

    return {
        "score":      score,
        "will_trade": score >= final_min_score,
        "min_required": final_min_score,
        "flags":      flags,
        "warnings":   warnings,
        "pool_data":  pool_data,
        "captured_state": captured_state
    }

def format_alert(result: dict, action: str) -> str:
    pool = result["pool_data"]
    score = result["score"]
    min_req = result["min_required"]
    emoji = "☸️ MAHORAGA BUY" if action == "buy" else "⚠️ ANALYSIS"
    
    lines = [
        f"{emoji}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Token: {pool['name']}",
        f"Score: {score}/{min_req} {'✅ TRADING' if result['will_trade'] else '❌ SKIPPING'}",
        f"CA   : {pool['token_mint']}",
        f"Chart: {pool['dex_url']}",
        f"━━━━━━━━━━━━━━━━━━━━",
    ]
    if result["flags"]:
        lines.append("✅ ADAPTED PROS:")
        lines += [f" • {f}" for f in result["flags"]]
    if result["warnings"]:
        lines.append("❌ ADAPTED CONS:")
        lines += [f" • {w}" for w in result["warnings"]]
    
    return "\n".join(lines)

async def run_scanner(on_buy_signal: Callable, notify: Callable):
    global scanner_running
    scanner_running = True
    log.info("Mahoraga Scanner v6.0 Started.")
    await notify("☸️ MAHORAGA ADAPTATION ONLINE\nSpinning the wheel...")

    while scanner_running:
        try:
            pools = fetch_new_solana_pools() + fetch_trending_solana_pools()
            seen = set()
            unique_pools = []
            for p in pools:
                addr = p.get("attributes", {}).get("address", "")
                if addr and addr not in seen:
                    seen.add(addr)
                    unique_pools.append(p)

            for raw_pool in unique_pools:
                pool = parse_pool(raw_pool)
                if not pool or pool["token_mint"] in analyzed_tokens:
                    continue
                
                analyzed_tokens.add(pool["token_mint"])
                
                if pool["liquidity_usd"] < MIN_LIQUIDITY_USD or pool["age_minutes"] > MAX_TOKEN_AGE_MIN:
                    continue

                result = score_token(pool)
                
                if result["will_trade"]:
                    alert = format_alert(result, action="buy")
                    await notify(alert)
                    await on_buy_signal(pool["token_mint"], pool, result["captured_state"])
                else:
                    # Only alert for high-ish scores to avoid spam
                    if result["score"] > 50:
                        alert = format_alert(result, action="alert")
                        await notify(alert)
                
                await asyncio.sleep(1)

        except Exception as e:
            log.error(f"Scanner error: {e}")
        
        await asyncio.sleep(SCAN_INTERVAL_SEC)

def stop_scanner():
    global scanner_running
    scanner_running = False
