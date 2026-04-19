"""X unofficial discovery fallback (EXPERIMENTAL ONLY).

This module is for experimental/local discovery use ONLY.
It must NEVER be required for production pipeline success.
It is always gated behind source.enabled = false in config.

No scraping of login-required pages is performed.
Only publicly visible tweet embeds are considered.
"""

from __future__ import annotations

import logging

from src.models import NormalizedItem, SourceConfig, TopicFilters

logger = logging.getLogger(__name__)


def collect(
    source: SourceConfig,
    filters: TopicFilters,
    max_items: int = 20,
) -> list[NormalizedItem]:
    """Experimental X discovery stub.

    Always returns an empty list. Exists only as a registered collector type
    so that config entries with type=x_unofficial are recognized and safely skipped.
    """
    logger.info(
        "X unofficial collector is experimental-only and returns no items (source: %s).",
        source.id,
    )
    return []
