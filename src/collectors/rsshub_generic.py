"""RSSHub generic collector.

RSSHub sources are fully optional. Failures do not abort the pipeline.
This collector reuses the generic RSS normalization logic.
"""

from __future__ import annotations

import logging

from src.collectors.rss_generic import collect as rss_collect
from src.models import NormalizedItem, SourceConfig, TopicFilters

logger = logging.getLogger(__name__)


def collect(
    source: SourceConfig,
    filters: TopicFilters,
    user_agent: str,
    max_items: int = 20,
) -> list[NormalizedItem]:
    """Collect from an RSSHub-hosted feed.

    Delegates to the generic RSS collector since RSSHub serves standard RSS/Atom.
    If the source has no feed_urls configured, returns an empty list.
    """
    if not source.feed_urls:
        logger.info("RSSHub source %s has no feed_urls configured, skipping.", source.id)
        return []

    logger.info("Fetching RSSHub source: %s (%d feeds)", source.id, len(source.feed_urls))
    try:
        return rss_collect(source=source, filters=filters, user_agent=user_agent, max_items=max_items)
    except Exception as exc:
        logger.warning("RSSHub collector failed for %s: %s", source.id, exc)
        return []
