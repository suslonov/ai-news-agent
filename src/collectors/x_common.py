"""Shared utilities for X/Twitter API collectors.

Both x_api.py and x_graph/scanner.py use these helpers.
All endpoint base URLs are injected from GlobalConfig (sources.yaml); nothing
is hardcoded here.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from typing import Optional

import httpx

from src.models import ItemStatus, NormalizedItem, SourceConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15


def get_bearer_token() -> Optional[str]:
    """Return X bearer token from environment, or None if not set."""
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    return token or None


def compute_hash(tweet_id: str) -> str:
    return hashlib.sha256(f"x_tweet_{tweet_id}".encode()).hexdigest()[:16]


def build_tweet_url(username: str, tweet_id: str, tweet_base_url: str) -> str:
    return f"{tweet_base_url}/{username}/status/{tweet_id}"


def fetch_user_tweets(
    handle: str,
    bearer_token: str,
    api_base: str,
    max_results: int = 10,
) -> list[dict]:
    """Fetch recent tweets for *handle* via the v2 user timeline endpoint.

    Returns an empty list on any error so callers remain resilient.
    Clamps max_results to the API maximum of 100.
    """
    headers = {"Authorization": f"Bearer {bearer_token}"}
    try:
        resp = httpx.get(
            f"{api_base}/users/by/username/{handle}",
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        user_id = resp.json()["data"]["id"]

        resp = httpx.get(
            f"{api_base}/users/{user_id}/tweets",
            headers=headers,
            params={
                "max_results": min(max_results, 100),
                "tweet.fields": "created_at,author_id,text",
                "expansions": "author_id",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        tweets = resp.json().get("data", [])
        for t in tweets:
            t["_username"] = handle
        return tweets
    except Exception as exc:
        logger.warning("Failed to fetch tweets for @%s: %s", handle, exc)
        return []


def normalize_tweet(
    tweet: dict,
    source: SourceConfig,
    tweet_base_url: str,
    extra_tags: Optional[list[str]] = None,
) -> Optional[NormalizedItem]:
    """Normalize a raw API v2 tweet object into a NormalizedItem.

    *extra_tags* are appended to source.tags (e.g. ["x", "twitter"]).
    Returns None if the tweet is missing id or text.
    """
    tweet_id = tweet.get("id", "")
    text = tweet.get("text", "").strip()
    username = tweet.get("_username", tweet.get("author_id", ""))

    if not tweet_id or not text:
        return None

    url = build_tweet_url(username, tweet_id, tweet_base_url)
    title = text[:120] + ("…" if len(text) > 120 else "")

    published_at: Optional[datetime] = None
    if created_at := tweet.get("created_at"):
        try:
            published_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except Exception:
            pass

    tags = list(source.tags)
    if extra_tags:
        tags.extend(extra_tags)

    return NormalizedItem(
        source_id=source.id,
        source_type=source.type.value,
        title=title,
        url=url,
        canonical_url=url,
        author=f"@{username}" if username else None,
        published_at=published_at,
        content_snippet=text[:500],
        hash=compute_hash(tweet_id),
        tags=tags,
        status=ItemStatus.candidate,
    )
