"""
sentiment.py — VADER Sentiment Analysis Engine.
Used to score social media posts/comments.
"""
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Pre-load the analyzer for speed
analyzer = SentimentIntensityAnalyzer()

def get_sentiment_score(text: str) -> float:
    """
    Returns a compound sentiment score between -1.0 and 1.0.
    > 0.05: Positive
    < -0.05: Negative
    """
    if not text:
        return 0.0
    scores = analyzer.polarity_scores(text)
    return scores['compound']

def is_bullish(text: str, threshold: float = 0.2) -> bool:
    """Check if the text sentiment is bullish based on a threshold."""
    return get_sentiment_score(text) >= threshold
