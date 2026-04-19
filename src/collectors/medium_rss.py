"""Medium RSS collector.

Uses RSS for discovery. Normalizes entries and optionally marks items
for browser-based enrichment if configured in the source.
"""

from __future__ import annotations

import logging

from src.collectors.rss_generic import collect as rss_collect, normalize_entry, fetch_feed
from src.models import ItemStatus, NormalizedItem, SourceConfig, TopicFilters

logger = logging.getLogger(__name__)


def collect(
    source: SourceConfig,
    filters: TopicFilters,
    max_items: int = 20,
) -> list[NormalizedItem]:
    """Collect and normalize entries from Medium RSS feeds.

    Medium RSS feeds are standard Atom/RSS and are handled by the generic collector.
    Items eligible for browser enrichment are flagged in their tags list with
    'medium_browser_eligible' if source.enrich_with_browser_if_selected is True.
    """
    if not source.feed_urls:
        logger.info("Medium source %s has no feed_urls configured, skipping.", source.id)
        return []

    items = rss_collect(source=source, filters=filters, max_items=max_items)

    if source.enrich_with_browser_if_selected:
        enriched = []
        for item in items:
            tags = list(item.tags)
            if "medium_browser_eligible" not in tags:
                tags.append("medium_browser_eligible")
            item = item.model_copy(update={"tags": tags})
            enriched.append(item)
        return enriched

    return items
