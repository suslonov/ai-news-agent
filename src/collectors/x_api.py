"""X (Twitter) API collector.

Production execution is gated behind:
- source.enabled = true
- ENABLE_X_PRODUCTION environment variable = "true"

This module is kept in the codebase but disabled by default.
"""

from __future__ import annotations

import logging

import httpx

from src.collectors.x_common import (
    DEFAULT_TIMEOUT,
    fetch_user_tweets,
    get_bearer_token,
    normalize_tweet,
)
from src.models import GlobalConfig, NormalizedItem, SourceConfig, TopicFilters

logger = logging.getLogger(__name__)


def _fetch_search(
    query: str,
    bearer_token: str,
    api_base: str,
    max_results: int = 10,
) -> list[dict]:
    """Search recent tweets using the v2 search endpoint."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    try:
        resp = httpx.get(
            f"{api_base}/tweets/search/recent",
            headers=headers,
            params={
                "query": query,
                "max_results": max_results,
                "tweet.fields": "created_at,author_id,text",
                "expansions": "author_id",
            },
            timeout=DEFAULT_TIMEOUT,
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
    global_config: GlobalConfig,
    max_items: int = 20,
) -> list[NormalizedItem]:
    """Collect tweets from X API based on source type.

    Returns an empty list if bearer token is missing.
    Production gating is enforced by the pipeline before this function is called.
    """
    bearer_token = get_bearer_token()
    if not bearer_token:
        logger.warning("X_BEARER_TOKEN not set. Skipping source %s.", source.id)
        return []

    api_base = global_config.x_api_base_url
    tweet_base_url = global_config.x_tweet_base_url
    raw_tweets: list[dict] = []

    if source.type.value == "x_api_accounts":
        for username in source.usernames:
            raw_tweets.extend(
                fetch_user_tweets(username, bearer_token, api_base, max_results=5)
            )

    elif source.type.value == "x_api_search":
        # Twitter v2 recent search requires max_results in [10, 100].
        # Use source.max_results if set, otherwise derive from max_items,
        # always clamping to the API minimum of 10.
        per_query = source.max_results or max(1, max_items // max(len(source.queries), 1))
        per_query = max(10, per_query)
        for query in source.queries:
            raw_tweets.extend(
                _fetch_search(query, bearer_token, api_base, max_results=per_query)
            )

    results: list[NormalizedItem] = []
    for tweet in raw_tweets:
        item = normalize_tweet(tweet, source, tweet_base_url)
        if item:
            results.append(item)
        if len(results) >= max_items:
            break

    logger.info("X collector %s: collected %d items", source.id, len(results))
    return results
