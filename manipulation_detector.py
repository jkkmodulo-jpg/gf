"""
manipulation_detector.py — Detects pump & dump and coordinated manipulation.
Core rule: HYPE WITHOUT VOLUME = TRAP.
Tracks sentiment velocity, volume vs hype ratio, and price action patterns.
"""
import time
import logging
from collections import deque

log = logging.getLogger("manipulation")

# ── THRESHOLDS ────────────────────────────────────────────────────────────────
SENTIMENT_SPIKE_THRESHOLD   = 0.4    # sentiment rising this fast in 5min = suspicious
VOLUME_HYPE_MIN_RATIO       = 0.3    # volume must be at least 30% of hype signal
PRICE_PUMP_THRESHOLD        = 150.0  # % price change in 1hr = already pumped
MAX_SAFE_TOP_HOLDER_PCT     = 15.0   # top holder above this = manipulation risk
SENTIMENT_WINDOW_SEC        = 300    # 5 minute window for velocity checks

# ── HISTORY TRACKING ─────────────────────────────────────────────────────────
# ca → deque of (timestamp, sentiment_score)
_sentiment_history: dict[str, deque] = {}

# ca → deque of (timestamp, mention_count)
_mention_history: dict[str, deque] = {}

def _get_history(ca: str) -> deque:
    if ca not in _sentiment_history:
        _sentiment_history[ca] = deque(maxlen=50)
    return _sentiment_history[ca]

def record_mention(ca: str, sentiment: float):
    """Call this every time a new mention is detected for a token."""
    history = _get_history(ca)
    history.append((time.time(), sentiment))

# ── VELOCITY DETECTION ────────────────────────────────────────────────────────

def _get_recent_sentiments(ca: str, window_sec: int = SENTIMENT_WINDOW_SEC) -> list[float]:
    """Get sentiment scores within the recent time window."""
    history = _get_history(ca)
    cutoff = time.time() - window_sec
    return [s for ts, s in history if ts >= cutoff]

def get_sentiment_velocity(ca: str) -> float:
    """
    How fast is sentiment rising?
    Returns rate of change per minute. High = suspicious.
    """
    history = _get_history(ca)
    if len(history) < 2:
        return 0.0

    now = time.time()
    recent = [(ts, s) for ts, s in history if now - ts <= SENTIMENT_WINDOW_SEC]
    if len(recent) < 2:
        return 0.0

    oldest_s = recent[0][1]
    newest_s = recent[-1][1]
    time_diff_min = (recent[-1][0] - recent[0][0]) / 60

    if time_diff_min < 0.1:
        return 0.0

    return (newest_s - oldest_s) / time_diff_min

# ── MAIN DETECTION ────────────────────────────────────────────────────────────

def check_manipulation(
    ca: str,
    pool_data: dict,
    captured_state: dict,
    current_sentiment: float = 0.0
) -> tuple[bool, str]:
    """
    Full manipulation check for a token before buying.
    Returns: (is_safe, reason)
    """

    # 1. Already pumped check
    price_change_1h = pool_data.get("price_change_1h", 0)
    if price_change_1h > PRICE_PUMP_THRESHOLD:
        reason = f"Already pumped {price_change_1h:.0f}% in 1hr — likely top"
        log.warning(f"MANIPULATION: {ca[:8]} — {reason}")
        return False, reason

    # 2. Hype without volume check
    liquidity = pool_data.get("liquidity_usd", 0)
    volume_1h = pool_data.get("volume_1h", 0)
    vol_liq_ratio = volume_1h / liquidity if liquidity > 0 else 0

    if current_sentiment > 0.5 and vol_liq_ratio < VOLUME_HYPE_MIN_RATIO:
        reason = f"High hype but low volume (vol/liq={vol_liq_ratio:.2f}) — fake pump signal"
        log.warning(f"MANIPULATION: {ca[:8]} — {reason}")
        return False, reason

    # 3. Sentiment velocity check (too-perfect, too-fast rise)
    velocity = get_sentiment_velocity(ca)
    if velocity > SENTIMENT_SPIKE_THRESHOLD:
        reason = f"Sentiment spiking too fast ({velocity:.3f}/min) — coordinated push"
        log.warning(f"MANIPULATION: {ca[:8]} — {reason}")
        return False, reason

    # 4. Whale concentration check
    top_holder_pct = captured_state.get("top_holder_pct", 0)
    if top_holder_pct > MAX_SAFE_TOP_HOLDER_PCT:
        reason = f"Top holder owns {top_holder_pct:.1f}% — dump risk"
        log.warning(f"MANIPULATION: {ca[:8]} — {reason}")
        return False, reason

    # 5. Liquidity too low for the hype
    if current_sentiment > 0.6 and liquidity < 5000:
        reason = f"High sentiment but tiny liquidity (${liquidity:.0f}) — honeypot risk"
        log.warning(f"MANIPULATION: {ca[:8]} — {reason}")
        return False, reason

    return True, "No manipulation detected"

def is_pump_and_dump(pool_data: dict) -> tuple[bool, str]:
    """
    Quick check: is this token already in a pump and dump cycle?
    Looks at price action pattern alone.
    """
    price_change = pool_data.get("price_change_1h", 0)
    volume = pool_data.get("volume_1h", 0)
    liquidity = pool_data.get("liquidity_usd", 1)
    age = pool_data.get("age_minutes", 999)

    # Classic pump: new token, huge price spike, volume exceeds liquidity
    if age < 30 and price_change > 200 and volume > liquidity * 2:
        return True, f"Classic pump pattern: {price_change:.0f}% rise in {age:.0f}min"

    return False, "No pump pattern detected"

def get_risk_level(ca: str, pool_data: dict, captured_state: dict) -> str:
    """Returns 'LOW', 'MEDIUM', or 'HIGH' manipulation risk."""
    price_change = pool_data.get("price_change_1h", 0)
    top_holder = captured_state.get("top_holder_pct", 0)
    velocity = get_sentiment_velocity(ca)

    risk_score = 0
    if price_change > 100: risk_score += 2
    if price_change > 200: risk_score += 2
    if top_holder > 10: risk_score += 2
    if top_holder > 20: risk_score += 2
    if velocity > 0.2: risk_score += 1
    if velocity > 0.4: risk_score += 2

    if risk_score >= 6: return "HIGH"
    if risk_score >= 3: return "MEDIUM"
    return "LOW"
