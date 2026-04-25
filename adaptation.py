"""
adaptation.py — The "Mahoraga Wheel" Engine.
Learns from losses and adapts scoring weights dynamically.
"""
import json
import os
import logging

ADAPTATION_FILE = "adaptation.json"
log = logging.getLogger("adaptation")

DEFAULT_ADAPTATION = {
    "loss_patterns": [],
    "dynamic_penalties": {
        "high_top_holder": 0,
        "low_liquidity": 0,
        "high_vol_liq_ratio": 0,
        "new_token_risk": 0
    },
    "market_defense_level": 0 # 0 to 20, increases MIN_SCORE_TO_BUY
}

def load_adaptation() -> dict:
    if not os.path.exists(ADAPTATION_FILE):
        return DEFAULT_ADAPTATION
    try:
        with open(ADAPTATION_FILE, "r") as f:
            return json.load(f)
    except:
        return DEFAULT_ADAPTATION

def save_adaptation(data: dict):
    with open(ADAPTATION_FILE, "w") as f:
        json.dump(data, f, indent=2)

def spin_the_wheel(trade_data: dict):
    """
    Analyze a closed trade. If it was a loss, adapt the defense.
    """
    adaptation = load_adaptation()
    pnl = trade_data.get("pnl_sol", 0)
    
    if pnl < 0:
        log.info("MAHORAGA: Loss detected. Spinning the wheel...")
        # Extract what went wrong from the trade state (captured during buy)
        state = trade_data.get("captured_state", {})
        
        # 1. Adapt to Top Holder concentration
        top_holder = state.get("top_holder_pct", 0)
        if top_holder > 5:
            adaptation["dynamic_penalties"]["high_top_holder"] += 2
            log.info(f"Adapted: Increased penalty for top holders (Current: -{adaptation['dynamic_penalties']['high_top_holder']})")

        # 2. Adapt to Liquidity levels
        liq = state.get("liquidity_usd", 0)
        if liq < 15000:
            adaptation["dynamic_penalties"]["low_liquidity"] += 2
            log.info(f"Adapted: Increased penalty for low liquidity")

        # 3. Increase Market Defense (Raise the bar for all tokens)
        adaptation["market_defense_level"] = min(30, adaptation["market_defense_level"] + 2)
        log.info(f"Adapted: Market Defense Level raised to {adaptation['market_defense_level']}")

    else:
        log.info("MAHORAGA: Win detected. Maintaining current adaptation.")
        # Slightly lower defense on wins to stay aggressive
        adaptation["market_defense_level"] = max(0, adaptation["market_defense_level"] - 1)

    save_adaptation(adaptation)

def get_dynamic_score_adjustment(pool_data: dict, state: dict) -> int:
    """Calculate penalty based on learned patterns."""
    adaptation = load_adaptation()
    penalty = 0
    
    # Apply learned penalties
    if state.get("top_holder_pct", 0) > 5:
        penalty += adaptation["dynamic_penalties"]["high_top_holder"]
        
    if pool_data.get("liquidity_usd", 0) < 15000:
        penalty += adaptation["dynamic_penalties"]["low_liquidity"]
        
    return -penalty

def get_market_defense_bonus() -> int:
    """Returns the current defense level to be added to MIN_SCORE_TO_BUY."""
    return load_adaptation().get("market_defense_level", 0)
