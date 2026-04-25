"""
subscription.py — Manages user subscriptions for the Mahoraga bot.
3-day free trial. Three tiers: Basic $15, Pro $24, Elite $60/month.
File-based storage — no database needed.
Integrates with Telegram user IDs.
"""
import json
import os
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

SUBSCRIPTIONS_FILE = "subscriptions.json"
log = logging.getLogger("subscription")

# ── TIER DEFINITIONS ──────────────────────────────────────────────────────────
TIERS = {
    "trial": {
        "name": "Free Trial",
        "price_monthly": 0,
        "duration_days": 3,
        "signals_per_day": 999,     # full access during trial
        "early_signals": True,
        "all_signals": True,
        "description": "Full Pro access for 3 days — no card needed"
    },
    "basic": {
        "name": "Basic",
        "price_monthly": 15,
        "duration_days": 30,
        "signals_per_day": 10,      # top 10 signals only
        "early_signals": False,
        "all_signals": False,
        "description": "Top 10 signals per day"
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 24,
        "duration_days": 30,
        "signals_per_day": 999,
        "early_signals": False,
        "all_signals": True,
        "description": "All signals + faster execution"
    },
    "elite": {
        "name": "Elite",
        "price_monthly": 60,
        "duration_days": 30,
        "signals_per_day": 999,
        "early_signals": True,
        "all_signals": True,
        "description": "Everything + early signals first"
    }
}

# ── STORAGE ───────────────────────────────────────────────────────────────────

def _load() -> dict:
    if not os.path.exists(SUBSCRIPTIONS_FILE):
        return {"users": {}}
    with open(SUBSCRIPTIONS_FILE) as f:
        return json.load(f)

def _save(data: dict):
    with open(SUBSCRIPTIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── USER MANAGEMENT ───────────────────────────────────────────────────────────

def register_user(telegram_id: int, wallet_address: str = "") -> dict:
    """
    Register a new user with a 3-day free trial.
    Returns the new user record.
    """
    data = _load()
    uid = str(telegram_id)

    if uid in data["users"]:
        return data["users"][uid]

    now = time.time()
    trial_end = now + (TIERS["trial"]["duration_days"] * 86400)

    user = {
        "telegram_id": telegram_id,
        "wallet_address": wallet_address,
        "tier": "trial",
        "trial_used": True,
        "subscribed_at": now,
        "expires_at": trial_end,
        "signals_today": 0,
        "signals_reset_day": datetime.now().strftime("%Y-%m-%d"),
        "total_signals_sent": 0,
        "joined_at": now
    }

    data["users"][uid] = user
    _save(data)
    log.info(f"New user registered: {telegram_id} (trial until {datetime.fromtimestamp(trial_end).strftime('%Y-%m-%d')})")
    return user

def subscribe(telegram_id: int, tier: str) -> tuple[bool, str]:
    """
    Upgrade or set a user's subscription tier.
    Returns (success, message)
    In production: integrate payment processor here before calling this.
    """
    if tier not in TIERS:
        return False, f"Unknown tier. Choose: basic, pro, elite"

    data = _load()
    uid = str(telegram_id)

    if uid not in data["users"]:
        register_user(telegram_id)
        data = _load()

    now = time.time()
    duration = TIERS[tier]["duration_days"] * 86400
    data["users"][uid]["tier"] = tier
    data["users"][uid]["subscribed_at"] = now
    data["users"][uid]["expires_at"] = now + duration
    _save(data)

    tier_info = TIERS[tier]
    msg = (f"✅ Subscribed to {tier_info['name']} (${tier_info['price_monthly']}/mo)\n"
           f"{tier_info['description']}\n"
           f"Expires: {datetime.fromtimestamp(now + duration).strftime('%Y-%m-%d')}")
    return True, msg

def get_user(telegram_id: int) -> Optional[dict]:
    """Get user record. Returns None if not registered."""
    data = _load()
    return data["users"].get(str(telegram_id))

def is_active(telegram_id: int) -> bool:
    """Check if user has an active subscription or trial."""
    user = get_user(telegram_id)
    if not user:
        return False
    return time.time() < user.get("expires_at", 0)

def get_tier(telegram_id: int) -> Optional[str]:
    """Get the user's current tier name."""
    user = get_user(telegram_id)
    if not user or not is_active(telegram_id):
        return None
    return user.get("tier")

# ── SIGNAL GATE ───────────────────────────────────────────────────────────────

def can_receive_signal(telegram_id: int, is_early: bool = False) -> tuple[bool, str]:
    """
    Check if user is allowed to receive a signal right now.
    Returns (allowed, reason)
    """
    user = get_user(telegram_id)
    if not user:
        return False, "Not registered. Send /start to begin your free trial."

    if not is_active(telegram_id):
        tier = user.get("tier", "trial")
        if tier == "trial":
            return False, "Your 3-day free trial has expired.\nSubscribe to continue:\n/subscribe basic — $15/mo\n/subscribe pro — $24/mo\n/subscribe elite — $60/mo"
        return False, "Your subscription has expired. Renew with /subscribe"

    tier_name = user.get("tier", "basic")
    tier_config = TIERS.get(tier_name, TIERS["basic"])

    # Early signals gating
    if is_early and not tier_config["early_signals"]:
        return False, f"Early signals require Elite tier ($60/mo)"

    # Daily signal limit (Basic tier)
    today = datetime.now().strftime("%Y-%m-%d")
    if user.get("signals_reset_day") != today:
        user["signals_today"] = 0
        user["signals_reset_day"] = today

    max_signals = tier_config["signals_per_day"]
    if user["signals_today"] >= max_signals:
        return False, f"Daily signal limit reached ({max_signals}/day on {tier_config['name']} tier). Upgrade for more."

    return True, "ok"

def record_signal_sent(telegram_id: int):
    """Increment the user's daily signal count."""
    data = _load()
    uid = str(telegram_id)
    if uid not in data["users"]:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    if data["users"][uid].get("signals_reset_day") != today:
        data["users"][uid]["signals_today"] = 0
        data["users"][uid]["signals_reset_day"] = today
    data["users"][uid]["signals_today"] = data["users"][uid].get("signals_today", 0) + 1
    data["users"][uid]["total_signals_sent"] = data["users"][uid].get("total_signals_sent", 0) + 1
    _save(data)

# ── STATS ─────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Admin stats — total users, active, tier breakdown."""
    data = _load()
    users = list(data["users"].values())
    now = time.time()

    active = [u for u in users if now < u.get("expires_at", 0)]
    trial_users = [u for u in active if u["tier"] == "trial"]
    basic_users = [u for u in active if u["tier"] == "basic"]
    pro_users = [u for u in active if u["tier"] == "pro"]
    elite_users = [u for u in active if u["tier"] == "elite"]

    mrr = (len(basic_users) * 15) + (len(pro_users) * 24) + (len(elite_users) * 60)

    return {
        "total_users": len(users),
        "active_users": len(active),
        "on_trial": len(trial_users),
        "basic": len(basic_users),
        "pro": len(pro_users),
        "elite": len(elite_users),
        "mrr_usd": mrr
    }

def get_user_status_message(telegram_id: int) -> str:
    """Formatted status message for /mystatus command."""
    user = get_user(telegram_id)
    if not user:
        return "You are not registered. Send /start to begin your 3-day free trial."

    tier = user.get("tier", "trial")
    tier_info = TIERS.get(tier, TIERS["basic"])
    expires = datetime.fromtimestamp(user.get("expires_at", 0)).strftime("%Y-%m-%d")
    active = is_active(telegram_id)

    status = "✅ Active" if active else "❌ Expired"
    return (
        f"📊 Your Subscription\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Tier: {tier_info['name']}\n"
        f"Status: {status}\n"
        f"Expires: {expires}\n"
        f"Signals today: {user.get('signals_today', 0)}\n"
        f"Total signals: {user.get('total_signals_sent', 0)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Upgrade: /subscribe basic|pro|elite"
    )
