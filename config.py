"""
config.py — All configuration loaded from .env
Import this everywhere instead of repeating os.getenv calls.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
TELEGRAM_API_ID      = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH    = os.getenv("TELEGRAM_API_HASH", "")
YOUR_TELEGRAM_ID     = int(os.getenv("YOUR_TELEGRAM_ID", "0"))
TELEGRAM_SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING", "")  # StringSession for Railway

def _parse_channel(c: str):
    try:
        return int(c)       # numeric ID (private groups)
    except ValueError:
        return c            # @username (public channels)

_raw = os.getenv("CHANNELS", "")
CHANNELS = [_parse_channel(c.strip()) for c in _raw.split(",") if c.strip()]

# ── REDDIT (Social Sentiment) ────────────────────────────────────────────────
REDDIT_CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT    = os.getenv("REDDIT_USER_AGENT", "MemeBot/1.0")
REDDIT_SUBREDDITS    = os.getenv("REDDIT_SUBREDDITS", "solana,cryptomoonshots,wallstreetbets").split(",")

# ── SOLANA ────────────────────────────────────────────────────────────────────
SOLANA_PRIVATE_KEY   = os.getenv("SOLANA_PRIVATE_KEY", "")
RPC_URL              = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
TRADE_AMOUNT_SOL     = float(os.getenv("TRADE_AMOUNT_SOL", "0.05"))
SLIPPAGE_BPS         = int(os.getenv("SLIPPAGE_BPS", "500"))
AUTO_SELL_MULTIPLIER = float(os.getenv("AUTO_SELL_MULTIPLIER", "2"))
STOP_LOSS_PERCENT    = float(os.getenv("STOP_LOSS_PERCENT", "30"))

# ── KEYWORD FILTERS ───────────────────────────────────────────────────────────
_wl = os.getenv("WHITELIST_KEYWORDS", "")
WHITELIST_KEYWORDS = [k.strip().lower() for k in _wl.split(",") if k.strip()]

_bl = os.getenv("BLACKLIST_KEYWORDS", "")
BLACKLIST_KEYWORDS = [k.strip().lower() for k in _bl.split(",") if k.strip()]

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
SOL_MINT         = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000

KNOWN_PROGRAMS = {
    "So11111111111111111111111111111111111111112",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bN",
    "11111111111111111111111111111111",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
}
