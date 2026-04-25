"""
reddit_monitor.py — Streams live Reddit posts to find early meme signals.
"""
import praw
import logging
import asyncio
import re
from typing import Callable
from config import (
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_USER_AGENT,
    REDDIT_SUBREDDITS
)
from sentiment import get_sentiment_score

log = logging.getLogger("reddit")

# Solana CA Regex (32-44 chars)
SOLANA_CA_PATTERN = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')

class RedditMonitor:
    def __init__(self):
        self.reddit = None
        if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
            self.reddit = praw.Reddit(
                client_id=REDDIT_CLIENT_ID,
                client_secret=REDDIT_CLIENT_SECRET,
                user_agent=REDDIT_USER_AGENT
            )
        self.running = False

    async def run(self, on_signal: Callable):
        if not self.reddit:
            log.warning("Reddit API keys missing. Reddit monitoring disabled.")
            return

        self.running = True
        log.info(f"Monitoring subreddits: {', '.join(REDDIT_SUBREDDITS)}")

        # PRAW streams are blocking, so we run them in a thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._stream_posts, on_signal)

    def _stream_posts(self, on_signal: Callable):
        try:
            subreddit = self.reddit.subreddit("+".join(REDDIT_SUBREDDITS))
            for submission in subreddit.stream.submissions(skip_existing=True):
                if not self.running:
                    break
                
                text = f"{submission.title} {submission.selftext}"
                cas = SOLANA_CA_PATTERN.findall(text)
                
                if cas:
                    score = get_sentiment_score(text)
                    for ca in set(cas):
                        log.info(f"Reddit Signal: {ca} | Sentiment: {score:.2f} | Sub: r/{submission.subreddit}")
                        # Fire signal if sentiment is not negative
                        if score >= 0:
                            asyncio.run_coroutine_threadsafe(
                                on_signal(ca, score, f"reddit (r/{submission.subreddit})"),
                                asyncio.get_event_loop()
                            )
        except Exception as e:
            log.error(f"Reddit stream error: {e}")
            self.running = False

    def stop(self):
        self.running = False
