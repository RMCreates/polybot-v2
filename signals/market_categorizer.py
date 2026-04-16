"""
Market categorizer — assigns a session label to any market question.
Used by edge_analyzer (signal tagging), paper_trader (capital routing),
and dashboard (display).
"""
from __future__ import annotations

SPORTS_KEYWORDS = [
    "nhl", "nba", "nfl", "mlb", "soccer", "championship", "stanley cup",
    "playoffs", "playoff", "series", "super bowl", "world cup", "match",
    "game 1", "game 2", "game 3", "game 4", "game 5", "game 6", "game 7",
    "finals", "semifinal", "quarterfinal", "tournament", "league",
    "first round", "second round", "conference", "division",
    "scores", "goal", "touchdown", "home run", "draft pick",
]

POLITICS_KEYWORDS = [
    "election", "president", "congress", "senate", "governor", "vote",
    "ballot", "legislation", "impeach", "candidate", "democrat", "republican",
    "primary", "approval", "poll", "trump", "biden", "harris", "political",
    "party", "house", "cabinet", "administration", "executive order",
    "supreme court", "justice", "tariff", "policy",
]

CRYPTO_KEYWORDS = [
    "bitcoin", "ethereum", "crypto", "btc", "eth", "solana", "sol",
    "price", "fed", "inflation", "gdp", "stock", "market cap",
    "interest rate", "dollar", "usd", "eur", "bond", "yield",
    "s&p", "nasdaq", "dow", "recession", "rate cut", "rate hike",
    "cpi", "pce", "jobs report", "nonfarm", "earnings",
]


def categorize_market(question: str) -> str:
    """
    Returns one of: "sports", "politics", "crypto", "longterm"
    based on keyword matching. Falls back to "longterm" when no category matches.
    """
    q = question.lower()
    sports_score = sum(1 for k in SPORTS_KEYWORDS if k in q)
    politics_score = sum(1 for k in POLITICS_KEYWORDS if k in q)
    crypto_score = sum(1 for k in CRYPTO_KEYWORDS if k in q)

    scores = {
        "sports": sports_score,
        "politics": politics_score,
        "crypto": crypto_score,
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "longterm"


def session_for_hours(hours_left: float, question: str) -> str:
    """
    Assign a market to a session based on time remaining:
      daily    — closes within 48h
      sports / politics / crypto — closes in 48h–168h (weekly window)
      longterm — closes in 168h–8760h (1 week to 1 year)
    """
    if hours_left <= 0:
        # Market already expired — don't route to daily, use keyword category
        return categorize_market(question)
    if hours_left <= 48.0:
        return "daily"
    if hours_left <= 168.0:
        return categorize_market(question)
    return "longterm"
