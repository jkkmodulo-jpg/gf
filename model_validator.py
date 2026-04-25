"""
model_validator.py — Prevents overfitting in the Mahoraga adaptation engine.
Validates that learned patterns still hold on recent data.
Automatically resets penalties that are hurting performance.
"""
import json
import os
import logging
import time
from typing import Optional

POSITIONS_FILE   = "positions.json"
ADAPTATION_FILE  = "adaptation.json"
VALIDATION_FILE  = "validation_state.json"

log = logging.getLogger("validator")

# ── THRESHOLDS ────────────────────────────────────────────────────────────────
MIN_TRADES_TO_VALIDATE    = 20     # need at least this many before validating
RECENT_WINDOW_DAYS        = 90     # only trust patterns from last 90 days
MAX_PENALTY_VALUE         = 20     # cap any single penalty at this
MAX_DEFENSE_LEVEL         = 25     # cap defense level here (not too defensive)
WIN_RATE_DROP_THRESHOLD   = 0.10   # if win rate drops 10%+ after adaptation → reset
VALIDATION_INTERVAL_SEC   = 60 * 60 * 6   # validate every 6 hours

# ── LOAD / SAVE ───────────────────────────────────────────────────────────────

def _load_positions() -> dict:
    if not os.path.exists(POSITIONS_FILE):
        return {"positions": {}, "trades": []}
    with open(POSITIONS_FILE) as f:
        return json.load(f)

def _load_adaptation() -> dict:
    if not os.path.exists(ADAPTATION_FILE):
        return {}
    with open(ADAPTATION_FILE) as f:
        return json.load(f)

def _save_adaptation(data: dict):
    with open(ADAPTATION_FILE, "w") as f:
        json.dump(data, f, indent=2)

def _load_validation_state() -> dict:
    if not os.path.exists(VALIDATION_FILE):
        return {"last_win_rate": None, "last_checked": 0, "resets": 0}
    with open(VALIDATION_FILE) as f:
        return json.load(f)

def _save_validation_state(data: dict):
    with open(VALIDATION_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── ANALYSIS ──────────────────────────────────────────────────────────────────

def _get_recent_trades(days: int = RECENT_WINDOW_DAYS) -> list[dict]:
    """Get only recent closed trades."""
    data = _load_positions()
    trades = data.get("trades", [])
    cutoff = time.time() - (days * 86400)

    recent = []
    for t in trades:
        if t.get("action") != "sell":
            continue
        try:
            from datetime import datetime
            ts = datetime.fromisoformat(t["time"]).timestamp()
            if ts >= cutoff:
                recent.append(t)
        except Exception:
            pass
    return recent

def _calculate_win_rate(trades: list[dict]) -> Optional[float]:
    sells = [t for t in trades if t.get("pnl_sol") is not None]
    if len(sells) < MIN_TRADES_TO_VALIDATE:
        return None
    wins = sum(1 for t in sells if t["pnl_sol"] > 0)
    return wins / len(sells)

# ── VALIDATION ENGINE ─────────────────────────────────────────────────────────

def validate_and_fix() -> dict:
    """
    Main validation function.
    Checks if adaptation is helping or hurting.
    Returns a report dict.
    """
    report = {
        "checked_at": time.time(),
        "actions_taken": [],
        "win_rate": None,
        "status": "ok"
    }

    val_state = _load_validation_state()
    adaptation = _load_adaptation()

    if not adaptation:
        report["status"] = "no adaptation data yet"
        return report

    # 1. Cap penalties that have gone too high
    penalties = adaptation.get("dynamic_penalties", {})
    changed = False
    for key, val in penalties.items():
        if val > MAX_PENALTY_VALUE:
            penalties[key] = MAX_PENALTY_VALUE
            report["actions_taken"].append(f"Capped {key} penalty at {MAX_PENALTY_VALUE}")
            changed = True

    # 2. Cap defense level
    defense = adaptation.get("market_defense_level", 0)
    if defense > MAX_DEFENSE_LEVEL:
        adaptation["market_defense_level"] = MAX_DEFENSE_LEVEL
        report["actions_taken"].append(f"Capped defense level at {MAX_DEFENSE_LEVEL}")
        changed = True

    # 3. Check recent win rate
    recent_trades = _get_recent_trades(days=30)
    win_rate = _calculate_win_rate(recent_trades)
    report["win_rate"] = win_rate

    if win_rate is not None:
        last_win_rate = val_state.get("last_win_rate")

        # If win rate dropped significantly since last check → overfitting
        if last_win_rate is not None and (last_win_rate - win_rate) > WIN_RATE_DROP_THRESHOLD:
            log.warning(f"VALIDATOR: Win rate dropped {last_win_rate:.1%} → {win_rate:.1%}. Resetting penalties.")
            # Reset penalties halfway — don't wipe everything
            for key in penalties:
                penalties[key] = max(0, penalties[key] // 2)
            adaptation["market_defense_level"] = max(0, adaptation["market_defense_level"] - 5)
            val_state["resets"] = val_state.get("resets", 0) + 1
            report["actions_taken"].append(f"Reset penalties due to win rate drop ({last_win_rate:.1%} → {win_rate:.1%})")
            report["status"] = "reset"
            changed = True

        val_state["last_win_rate"] = win_rate

    # 4. Forget patterns older than RECENT_WINDOW_DAYS
    loss_patterns = adaptation.get("loss_patterns", [])
    if loss_patterns:
        cutoff = time.time() - (RECENT_WINDOW_DAYS * 86400)
        fresh = [p for p in loss_patterns if p.get("timestamp", 0) > cutoff]
        if len(fresh) < len(loss_patterns):
            removed = len(loss_patterns) - len(fresh)
            adaptation["loss_patterns"] = fresh
            report["actions_taken"].append(f"Forgot {removed} old patterns (>{RECENT_WINDOW_DAYS}d)")
            changed = True

    if changed:
        _save_adaptation(adaptation)

    val_state["last_checked"] = time.time()
    _save_validation_state(val_state)

    log.info(f"VALIDATOR: Check complete. Win rate: {win_rate}. Actions: {report['actions_taken']}")
    return report

def should_validate() -> bool:
    """Check if enough time has passed to run validation again."""
    val_state = _load_validation_state()
    last = val_state.get("last_checked", 0)
    return time.time() - last > VALIDATION_INTERVAL_SEC

def get_health_report() -> dict:
    """Quick health summary — called by /status command."""
    adaptation = _load_adaptation()
    val_state = _load_validation_state()
    recent = _get_recent_trades(days=30)
    win_rate = _calculate_win_rate(recent)

    return {
        "win_rate_30d": f"{win_rate:.1%}" if win_rate else "Not enough data",
        "trade_count_30d": len(recent),
        "defense_level": adaptation.get("market_defense_level", 0),
        "penalties": adaptation.get("dynamic_penalties", {}),
        "total_resets": val_state.get("resets", 0),
        "last_validated": val_state.get("last_checked", 0)
    }
