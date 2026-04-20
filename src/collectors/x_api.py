"""X (Twitter) API collector.

Production execution is gated behind:
- source.enabled = true
- ENABLE_X_PRODUCTION environment variable = "true"

This module is kept in the codebase but disabled by default.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.models import ImageSourceType, ItemStatus, NormalizedItem, SourceConfig, TopicFilters

logger = logging.getLogger(__name__)

_X_API_BASE = "https://api.twitter.com/2"
_DEFAULT_TIMEOUT = 15


def _is_production_enabled() -> bool:
    return os.environ.get("ENABLE_X_PRODUCTION", "false").lower() in ("true", "1", "yes")


def _get_bearer_token() -> Optional[str]:
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    return token or None


def _build_tweet_url(username: str, tweet_id: str) -> str:
    return f"https://twitter.com/{username}/status/{tweet_id}"


def _compute_hash(tweet_id: str) -> str:
    return hashlib.sha256(f"x_tweet_{tweet_id}".encode()).hexdigest()[:16]


def _normalize_tweet(tweet: dict, source: SourceConfig) -> Optional[NormalizedItem]:
    """Normalize a single Twitter API v2 tweet object into a NormalizedItem."""
    tweet_id = tweet.get("id", "")
    text = tweet.get("text", "").strip()
    author_id = tweet.get("author_id", "")
    created_at = tweet.get("created_at")

    # Resolve author username from includes if available
    username = tweet.get("_username", author_id)
    url = _build_tweet_url(username, tweet_id)

    if not tweet_id or not text:
        return None

    # Use first 120 chars of tweet text as title
    title = text[:120] + ("…" if len(text) > 120 else "")

    published_at: Optional[datetime] = None
    if created_at:
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
        author=f"@{username}" if username else None,
        published_at=published_at,
        content_snippet=text[:500],
        hash=_compute_hash(tweet_id),
        tags=list(source.tags),
        status=ItemStatus.candidate,
    )


def _fetch_user_timeline(username: str, bearer_token: str, max_results: int = 10) -> list[dict]:
    """Fetch recent tweets for a given username using the v2 API."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    try:
        # First resolve user id
        resp = httpx.get(
            f"{_X_API_BASE}/users/by/username/{username}",
            headers=headers,
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        user_id = resp.json()["data"]["id"]

        # Fetch timeline
        resp = httpx.get(
            f"{_X_API_BASE}/users/{user_id}/tweets",
            headers=headers,
            params={
                "max_results": max_results,
                "tweet.fields": "created_at,author_id,text",
                "expansions": "author_id",
            },
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        tweets = resp.json().get("data", [])
        for t in tweets:
            t["_username"] = username
        return tweets
    except Exception as exc:
        logger.warning("Failed to fetch timeline for @%s: %s", username, exc)
        return []


def _fetch_search(query: str, bearer_token: str, max_results: int = 10) -> list[dict]:
    """Search recent tweets using the v2 search endpoint."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    try:
        resp = httpx.get(
            f"{_X_API_BASE}/tweets/search/recent",
            headers=headers,
            params={
                "query": query,
                "max_results": max_results,
                "tweet.fields": "created_at,author_id,text",
                "expansions": "author_id",
            },
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        tweets = data.get("data", [])
        users = {u["id"]: u["username"] for u in data.get("includes", {}).get("users", [])}
        for t in tweets:
            t["_username"] = users.get(t.get("author_id", ""), t.get("author_id", ""))
        return tweets
    except Exception as exc:
        logger.warning("X search failed for query '%s': %s", query, exc)
        return []


def collect(
    source: SourceConfig,
    filters: TopicFilters,
    max_items: int = 20,
) -> list[NormalizedItem]:
    """Collect tweets from X API based on source type.

    Returns an empty list if bearer token is missing.
    Production gating is enforced by the pipeline before this function is called.
    """
    bearer_token = _get_bearer_token()
    if not bearer_token:
        logger.warning("X_BEARER_TOKEN not set. Skipping source %s.", source.id)
        return []

    raw_tweets: list[dict] = []

    if source.type.value == "x_api_accounts":
        for username in source.usernames:
            raw_tweets.extend(_fetch_user_timeline(username, bearer_token, max_results=5))

    elif source.type.value == "x_api_search":
        for query in source.queries:
            raw_tweets.extend(_fetch_search(query, bearer_token, max_results=max_items // max(len(source.queries), 1)))

    results: list[NormalizedItem] = []
    for tweet in raw_tweets:
        item = _normalize_tweet(tweet, source)
        if item:
            results.append(item)
        if len(results) >= max_items:
            break

    logger.info("X collector %s: collected %d items", source.id, len(results))
    return results
