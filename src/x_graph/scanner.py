"""X/Twitter graph scanner — pipeline collector.

Reads the top-scored active accounts from twitter_accounts DB table and
fetches their recent tweets via the X API v2, returning NormalizedItems.

Production execution is gated behind ENABLE_X_PRODUCTION=true in the pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src import db
from src.collectors.x_common import fetch_user_tweets, get_bearer_token, normalize_tweet
from src.models import GlobalConfig, NormalizedItem, SourceConfig, TopicFilters

logger = logging.getLogger(__name__)

_EXTRA_TAGS = ["x", "twitter"]


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
    global_config: GlobalConfig,
    max_accounts: int = 20,
    max_items: int = 50,
) -> list[NormalizedItem]:
    """Collect tweets from the top graph accounts.

    Called by the pipeline when source.type == x_graph_scanner.
    Returns [] when the bearer token is missing.
    """
    bearer_token = get_bearer_token()
    if not bearer_token:
        logger.warning("X_BEARER_TOKEN not set. Graph scanner returning no items for %s.", source.id)
        return []

    handles = db.get_top_twitter_accounts(db_path, limit=max_accounts)
    if not handles:
        logger.info("No active accounts in twitter_accounts yet. Run build_x_graph first.")
        return []

    api_base = global_config.x_api_base_url
    tweet_base_url = global_config.x_tweet_base_url
    tweets_per_account = max(5, max_items // max(len(handles), 1))
    results: list[NormalizedItem] = []

    for handle in handles:
        raw_tweets = fetch_user_tweets(handle, bearer_token, api_base, max_results=tweets_per_account)
        for tweet in raw_tweets:
            item = normalize_tweet(tweet, source, tweet_base_url, extra_tags=_EXTRA_TAGS)
            if item and _passes_filters(item, filters):
                results.append(item)
            if len(results) >= max_items:
                break
        if len(results) >= max_items:
            break

    logger.info("X graph scanner: collected %d items from %d accounts", len(results), len(handles))
    return results
