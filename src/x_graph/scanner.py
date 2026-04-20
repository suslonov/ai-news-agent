"""X/Twitter graph scanner — pipeline collector.

Reads the top-scored active accounts from twitter_accounts DB table and
fetches their recent tweets via the X API v2, returning NormalizedItems.

Production execution is gated behind ENABLE_X_PRODUCTION=true in the pipeline.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from src import db
from src.models import ImageSourceType, ItemStatus, NormalizedItem, SourceConfig, TopicFilters

logger = logging.getLogger(__name__)

_X_API_BASE = "https://api.twitter.com/2"
_DEFAULT_TIMEOUT = 15


def _get_bearer_token() -> Optional[str]:
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    return token or None


def _compute_hash(tweet_id: str) -> str:
    return hashlib.sha256(f"x_tweet_{tweet_id}".encode()).hexdigest()[:16]


def _build_tweet_url(username: str, tweet_id: str) -> str:
    return f"https://twitter.com/{username}/status/{tweet_id}"


def _fetch_user_tweets(handle: str, bearer_token: str, max_results: int = 10) -> list[dict]:
    """Fetch recent tweets for an account. Returns [] on any error."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    try:
        resp = httpx.get(
            f"{_X_API_BASE}/users/by/username/{handle}",
            headers=headers,
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        user_id = resp.json()["data"]["id"]

        resp = httpx.get(
            f"{_X_API_BASE}/users/{user_id}/tweets",
            headers=headers,
            params={
                "max_results": min(max_results, 100),
                "tweet.fields": "created_at,author_id,text",
                "expansions": "author_id",
            },
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        tweets = resp.json().get("data", [])
        for t in tweets:
            t["_username"] = handle
        return tweets
    except Exception as exc:
        logger.warning("Graph scanner: failed to fetch tweets for @%s: %s", handle, exc)
        return []


def _normalize_tweet(tweet: dict, source: SourceConfig) -> Optional[NormalizedItem]:
    """Convert a raw API tweet object into a NormalizedItem."""
    tweet_id = tweet.get("id", "")
    text = tweet.get("text", "").strip()
    username = tweet.get("_username", tweet.get("author_id", ""))

    if not tweet_id or not text:
        return None

    url = _build_tweet_url(username, tweet_id)
    title = text[:120] + ("…" if len(text) > 120 else "")

    published_at: Optional[datetime] = None
    if created_at := tweet.get("created_at"):
        try:
            published_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except Exception:
            pass

    return NormalizedItem(
        source_id=source.id,
        source_type=source.type.value,
        title=title,
        url=url,
        canonical_url=url,
        author=f"@{username}",
        published_at=published_at,
        content_snippet=text[:500],
        image_source_type=ImageSourceType.none,
        hash=_compute_hash(tweet_id),
        tags=list(source.tags) + ["x", "twitter"],
        status=ItemStatus.candidate,
    )


def _passes_filters(item: NormalizedItem, filters: TopicFilters) -> bool:
    """Quick keyword check to avoid collecting completely off-topic tweets."""
    if not filters.include_keywords:
        return True
    text = (item.title + " " + (item.content_snippet or "")).lower()
    return any(kw.lower() in text for kw in filters.include_keywords)


def collect(
    source: SourceConfig,
    filters: TopicFilters,
    db_path: Path,
    max_accounts: int = 20,
    max_items: int = 50,
) -> list[NormalizedItem]:
    """Collect tweets from the top graph accounts.

    Called by the pipeline when source.type == x_graph_scanner.
    Returns [] when the bearer token is missing.
    """
    bearer_token = _get_bearer_token()
    if not bearer_token:
        logger.warning("X_BEARER_TOKEN not set. Graph scanner returning no items for %s.", source.id)
        return []

    handles = db.get_top_twitter_accounts(db_path, limit=max_accounts)
    if not handles:
        logger.info("No active accounts in twitter_accounts yet. Run build_x_graph first.")
        return []

    tweets_per_account = max(5, max_items // max(len(handles), 1))
    results: list[NormalizedItem] = []

    for handle in handles:
        raw_tweets = _fetch_user_tweets(handle, bearer_token, max_results=tweets_per_account)
        for tweet in raw_tweets:
            item = _normalize_tweet(tweet, source)
            if item and _passes_filters(item, filters):
                results.append(item)
            if len(results) >= max_items:
                break
        if len(results) >= max_items:
            break

    logger.info("X graph scanner: collected %d items from %d accounts", len(results), len(handles))
    return results
