"""
news_guard.py — Monitors news feeds for bad news about a token.
If dangerous news is detected, trading is FROZEN for that token.
Uses free RSS/GNews feeds — no paid API required.
"""
import time
import logging
import requests
import feedparser
from typing import Optional

log = logging.getLogger("news_guard")

# ── FREEZE STATE ──────────────────────────────────────────────────────────────
_frozen_tokens: dict[str, float] = {}     # ca → freeze_until timestamp
FREEZE_DURATION_SEC = 60 * 30             # 30 minutes freeze on bad news

# ── DANGER KEYWORDS ───────────────────────────────────────────────────────────
DANGER_KEYWORDS = [
    "rug pull", "rugpull", "exit scam", "honeypot", "hack", "hacked",
    "exploit", "fraud", "sec enforcement", "lawsuit", "arrested",
    "shutdown", "delisted", "bankrupt", "insolvent", "ponzi",
    "stolen funds", "breach", "compromised wallets"
]

# ── FREE NEWS FEEDS ───────────────────────────────────────────────────────────
NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
]

def _fetch_recent_headlines() -> list[str]:
    """Pull headlines from free crypto RSS feeds."""
    headlines = []
    for feed_url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:15]:  # only recent
                headlines.append((entry.get("title", "") + " " + entry.get("summary", "")).lower())
        except Exception as e:
            log.warning(f"Feed fetch failed {feed_url}: {e}")
    return headlines

def _search_gnews(query: str) -> list[str]:
    """Use GNews free tier to search by keyword."""
    try:
        resp = requests.get(
            "https://gnews.io/api/v4/search",
            params={"q": query, "lang": "en", "max": 5, "token": "free"},
            timeout=8
        )
        if resp.status_code == 200:
            articles = resp.json().get("articles", [])
            return [(a.get("title", "") + " " + a.get("description", "")).lower() for a in articles]
    except Exception:
        pass
    return []

def _contains_danger(text: str) -> bool:
    """Check if text contains any danger keywords."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in DANGER_KEYWORDS)

# ── PUBLIC API ────────────────────────────────────────────────────────────────

def is_frozen(ca: str) -> bool:
    """Returns True if this token is currently frozen due to bad news."""
    until = _frozen_tokens.get(ca)
    if until and time.time() < until:
        return True
    if ca in _frozen_tokens:
        del _frozen_tokens[ca]  # expired — clean up
    return False

def freeze_token(ca: str, reason: str = "news"):
    """Manually freeze a token from trading."""
    _frozen_tokens[ca] = time.time() + FREEZE_DURATION_SEC
    log.warning(f"NEWS GUARD: Token {ca[:8]}... FROZEN — {reason}")

def check_token(ca: str, token_name: str = "") -> tuple[bool, str]:
    """
    Check if a token has bad news.
    Returns: (is_safe, reason)
    """
    if is_frozen(ca):
        return False, "Token is frozen due to recent bad news"

    # Check RSS headlines for token name mentions
    if token_name:
        headlines = _fetch_recent_headlines()
        for headline in headlines:
            if token_name.lower() in headline and _contains_danger(headline):
                freeze_token(ca, f"Bad news: {headline[:80]}")
                return False, f"Danger keyword found in news: {headline[:80]}"

    return True, "No bad news detected"

def check_general_market() -> tuple[bool, str]:
    """
    Check if the overall crypto market has any systemic bad news.
    Returns (is_safe, reason)
    """
    headlines = _fetch_recent_headlines()
    danger_count = sum(1 for h in headlines if _contains_danger(h))

    if danger_count >= 8:
        reason = f"Market-wide danger detected ({danger_count} alerts in news)"
        log.warning(f"NEWS GUARD: {reason}")
        return False, reason

    return True, f"Market news clear ({danger_count} minor alerts)"

def get_frozen_count() -> int:
    """Return how many tokens are currently frozen."""
    now = time.time()
    return sum(1 for until in _frozen_tokens.values() if now < until)
