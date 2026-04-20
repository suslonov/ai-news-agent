"""Twitter/X graph builder.

Implements the seed → expand → score → prune lifecycle.

Production API calls are gated behind ENABLE_X_PRODUCTION=true and require
X_BEARER_TOKEN to be set. When disabled the builder still seeds the DB and
maintains the graph schema, but performs no live network requests.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

import httpx
import yaml

from src import db
from src.x_graph.models import TwitterSeedsConfig

logger = logging.getLogger(__name__)

_X_API_BASE = "https://api.twitter.com/2"
_DEFAULT_TIMEOUT = 15
_MENTION_RE = re.compile(r"@(\w{1,50})")

# Edge types used during expansion
_EDGE_MENTION = "mention"
_EDGE_RETWEET = "retweet"


def _is_x_enabled() -> bool:
    return os.environ.get("ENABLE_X_PRODUCTION", "false").lower() in ("true", "1", "yes")


def _get_bearer_token() -> Optional[str]:
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    return token or None


def load_seeds(seeds_path: Path) -> TwitterSeedsConfig:
    """Parse twitter_seeds.yaml and return the validated config."""
    raw = yaml.safe_load(seeds_path.read_text())
    return TwitterSeedsConfig(**raw)


def seed_db(db_path: Path, seeds_path: Path) -> int:
    """Idempotently insert seed accounts from twitter_seeds.yaml.

    Returns the number of accounts inserted or updated.
    """
    config = load_seeds(seeds_path)
    for seed in config.seeds:
        db.upsert_twitter_account(db_path, seed.handle, seed.category, source="seed")
    logger.info("Seeded %d accounts into twitter_accounts", len(config.seeds))
    return len(config.seeds)


def _fetch_user_tweets(handle: str, bearer_token: str, max_results: int = 50) -> list[dict]:
    """Fetch recent tweets for a user via X API v2.

    Returns raw tweet objects or [] on any error.
    """
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
                "tweet.fields": "created_at,author_id,text,referenced_tweets",
                "expansions": "author_id,referenced_tweets.id.author_id",
            },
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        tweets = data.get("data", [])
        # Attach username for convenience
        for t in tweets:
            t["_username"] = handle
        return tweets
    except Exception as exc:
        logger.warning("Failed to fetch tweets for @%s: %s", handle, exc)
        return []


def _extract_mentioned_handles(text: str) -> list[str]:
    """Extract @-mentioned handles from tweet text."""
    return [m.lower() for m in _MENTION_RE.findall(text) if len(m) >= 2]


def _extract_rt_handle(text: str) -> Optional[str]:
    """If the tweet is an RT, return the original author handle."""
    if text.startswith("RT @"):
        match = _MENTION_RE.search(text[3:])
        if match:
            return match.group(1).lower()
    return None


def expand_from_account(
    db_path: Path,
    handle: str,
    bearer_token: str,
    max_tweets: int = 50,
) -> int:
    """Fetch recent tweets for one account and record mention/RT edges.

    Returns the number of new edges recorded.
    """
    tweets = _fetch_user_tweets(handle, bearer_token, max_results=max_tweets)
    edges_recorded = 0
    for tweet in tweets:
        text = tweet.get("text", "")
        rt_handle = _extract_rt_handle(text)
        if rt_handle and rt_handle != handle.lower():
            db.record_twitter_edge(db_path, handle.lower(), rt_handle, _EDGE_RETWEET)
            edges_recorded += 1

        for mentioned in _extract_mentioned_handles(text):
            if mentioned != handle.lower():
                db.record_twitter_edge(db_path, handle.lower(), mentioned, _EDGE_MENTION)
                edges_recorded += 1

    return edges_recorded


def run_graph_build(
    db_path: Path,
    seeds_path: Path,
    max_accounts_to_expand: int = 30,
    max_tweets_per_account: int = 50,
    keep_count: int = 150,
    stale_days: int = 30,
) -> dict:
    """Full graph build cycle: seed → expand → score → prune.

    When ENABLE_X_PRODUCTION=false the function only seeds the DB (no API calls).
    Returns a summary dict with counts.
    """
    db.init_db(db_path)

    seeded = seed_db(db_path, seeds_path)
    summary: dict = {"seeded": seeded, "expanded": 0, "edges_added": 0, "pruned": 0}

    if not _is_x_enabled():
        logger.info(
            "ENABLE_X_PRODUCTION=false: graph expansion skipped. DB seeded with %d accounts.",
            seeded,
        )
        return summary

    bearer_token = _get_bearer_token()
    if not bearer_token:
        logger.warning("X_BEARER_TOKEN not set. Graph expansion skipped.")
        return summary

    handles_to_expand = db.get_top_twitter_accounts(db_path, limit=max_accounts_to_expand)
    logger.info("Expanding graph from %d accounts", len(handles_to_expand))

    total_edges = 0
    for handle in handles_to_expand:
        edges = expand_from_account(db_path, handle, bearer_token, max_tweets=max_tweets_per_account)
        total_edges += edges
        logger.debug("@%s: %d edges recorded", handle, edges)

    summary["expanded"] = len(handles_to_expand)
    summary["edges_added"] = total_edges

    db.update_twitter_scores(db_path)
    pruned = db.prune_twitter_accounts(db_path, keep_count=keep_count, stale_days=stale_days)
    summary["pruned"] = pruned

    logger.info(
        "Graph build complete: expanded=%d edges=%d pruned=%d",
        len(handles_to_expand),
        total_edges,
        pruned,
    )
    return summary
