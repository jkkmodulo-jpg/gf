"""
credibility_filter.py — Scores Reddit account credibility.
Filters out bot armies, new accounts, and coordinated fake sentiment.
Uses PRAW to check account details — no extra API needed.
"""
import time
import logging
from collections import Counter
from typing import Optional
import praw
from config import (
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_USER_AGENT
)

log = logging.getLogger("credibility")

# ── THRESHOLDS ────────────────────────────────────────────────────────────────
MIN_ACCOUNT_AGE_DAYS   = 30       # accounts younger than this = low trust
MIN_KARMA              = 100      # accounts below this = low trust
MAX_POSTS_PER_HOUR     = 10       # posting too fast = likely bot
COORDINATION_THRESHOLD = 5        # same phrase repeated X times = coordinated

# ── CACHE (avoid re-checking same accounts) ───────────────────────────────────
_account_cache: dict[str, dict] = {}   # username → {score, checked_at}
CACHE_TTL = 60 * 60 * 6                # 6 hours

# ── REDDIT CLIENT ─────────────────────────────────────────────────────────────
_reddit: Optional[praw.Reddit] = None

def _get_reddit() -> Optional[praw.Reddit]:
    global _reddit
    if _reddit:
        return _reddit
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return None
    try:
        _reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT
        )
        return _reddit
    except Exception as e:
        log.error(f"Reddit client error: {e}")
        return None

# ── ACCOUNT SCORING ───────────────────────────────────────────────────────────

def score_account(username: str) -> dict:
    """
    Score a Reddit account's credibility.
    Returns: {score (0-100), flags, is_trusted}
    """
    # Check cache first
    cached = _account_cache.get(username)
    if cached and time.time() - cached["checked_at"] < CACHE_TTL:
        return cached

    reddit = _get_reddit()
    if not reddit:
        return {"score": 50, "flags": ["Reddit unavailable"], "is_trusted": True}

    score = 100
    flags = []

    try:
        redditor = reddit.redditor(username)
        # Force load
        created_utc = redditor.created_utc
        comment_karma = redditor.comment_karma
        link_karma = redditor.link_karma
        total_karma = comment_karma + link_karma

        # Age check
        age_days = (time.time() - created_utc) / 86400
        if age_days < 7:
            score -= 50
            flags.append(f"New account ({int(age_days)}d old)")
        elif age_days < MIN_ACCOUNT_AGE_DAYS:
            score -= 25
            flags.append(f"Young account ({int(age_days)}d old)")

        # Karma check
        if total_karma < 10:
            score -= 40
            flags.append(f"Very low karma ({total_karma})")
        elif total_karma < MIN_KARMA:
            score -= 15
            flags.append(f"Low karma ({total_karma})")

        # Check recent post frequency (bot detection)
        try:
            recent_posts = list(redditor.new(limit=20))
            if len(recent_posts) >= 2:
                newest = recent_posts[0].created_utc
                oldest = recent_posts[-1].created_utc
                time_span_hours = max((newest - oldest) / 3600, 0.1)
                posts_per_hour = len(recent_posts) / time_span_hours
                if posts_per_hour > MAX_POSTS_PER_HOUR:
                    score -= 30
                    flags.append(f"Posting too fast ({posts_per_hour:.1f}/hr)")
        except Exception:
            pass

    except Exception as e:
        log.warning(f"Could not check account {username}: {e}")
        score = 40
        flags.append("Account check failed")

    score = max(0, min(100, score))
    result = {
        "score": score,
        "flags": flags,
        "is_trusted": score >= 50,
        "checked_at": time.time()
    }
    _account_cache[username] = result
    return result

# ── COORDINATION DETECTION ────────────────────────────────────────────────────

def detect_coordination(posts: list[dict]) -> tuple[bool, str]:
    """
    Detect if a group of posts looks coordinated (bot army).
    posts: list of {"text": str, "author": str}
    Returns: (is_coordinated, reason)
    """
    if len(posts) < 3:
        return False, "Not enough posts to analyse"

    # Check for repeated exact phrases
    phrases = []
    for post in posts:
        text = post.get("text", "").lower()
        words = text.split()
        for i in range(len(words) - 3):
            phrase = " ".join(words[i:i+4])
            phrases.append(phrase)

    phrase_counts = Counter(phrases)
    most_common = phrase_counts.most_common(1)
    if most_common and most_common[0][1] >= COORDINATION_THRESHOLD:
        return True, f"Coordinated phrase detected ({most_common[0][1]}x): '{most_common[0][0]}'"

    # Check for too many new accounts posting together
    if len(posts) >= 5:
        reddit = _get_reddit()
        if reddit:
            new_account_count = 0
            for post in posts[:10]:
                author = post.get("author", "")
                if author:
                    result = score_account(author)
                    if result["score"] < 50:
                        new_account_count += 1
            ratio = new_account_count / min(len(posts), 10)
            if ratio > 0.6:
                return True, f"High ratio of low-trust accounts ({int(ratio*100)}%)"

    return False, "No coordination detected"

# ── SENTIMENT WEIGHTING ───────────────────────────────────────────────────────

def weighted_sentiment(posts: list[dict]) -> float:
    """
    Calculate sentiment score weighted by account credibility.
    posts: list of {"text": str, "author": str, "sentiment": float}
    Returns: weighted average sentiment (-1.0 to 1.0)
    """
    if not posts:
        return 0.0

    total_weight = 0.0
    weighted_sum = 0.0

    for post in posts:
        author = post.get("author", "")
        sentiment = post.get("sentiment", 0.0)
        account_score = score_account(author)["score"] if author else 50
        weight = account_score / 100.0
        weighted_sum += sentiment * weight
        total_weight += weight

    if total_weight == 0:
        return 0.0

    return round(weighted_sum / total_weight, 4)
