"""Image URL resolution helpers following the policy in config/sources.yaml."""

from __future__ import annotations

import logging
from typing import Optional

from src.extraction import extract_first_article_image, extract_og_image, fetch_page
from src.models import ImagePolicy, ImageSourceType, NormalizedItem

logger = logging.getLogger(__name__)


def resolve_page_image(
    item: NormalizedItem,
    policy: ImagePolicy,
    user_agent: str,
    timeout: int = 8,
) -> tuple[Optional[str], ImageSourceType]:
    """Attempt to resolve a preview image URL for an item using its article page.

    Tries og:image and then first article image according to policy order.
    Returns (url, source_type) or (None, ImageSourceType.none).
    """
    if item.preview_image_url:
        return item.preview_image_url, item.image_source_type

    html = fetch_page(item.url, user_agent, timeout=timeout)
    if not html:
        return None, ImageSourceType.none

    order = policy.resolution_order
    for strategy in order:
        if strategy in ("media_thumbnail", "media_content", "enclosure"):
            # These are feed-level strategies handled by the RSS collector; skip at page level.
            continue
        elif strategy == "og_image":
            url = extract_og_image(html, item.url)
            if url:
                return url, ImageSourceType.og_image
        elif strategy == "first_reasonable_article_image":
            url = extract_first_article_image(html, item.url)
            if url:
                return url, ImageSourceType.first_article_image
        else:
            logger.debug("Unknown image resolution strategy %r, skipping.", strategy)

    return None, ImageSourceType.none


def enrich_items_with_images(
    items: list[NormalizedItem],
    policy: ImagePolicy,
    user_agent: str,
    max_fetches: int = 15,
    timeout: int = 8,
) -> tuple[list[NormalizedItem], int]:
    """Enrich a list of items with page-level image resolution.

    Only fetches pages for items that lack a preview_image_url.
    Respects max_fetches to bound network usage per run.

    Returns (enriched_items, resolved_count).
    """
    fetches = 0
    resolved = 0
    enriched: list[NormalizedItem] = []

    for item in items:
        if item.preview_image_url:
            enriched.append(item)
            continue

        if fetches >= max_fetches:
            enriched.append(item)
            continue

        fetches += 1
        url, src_type = resolve_page_image(item, policy, user_agent, timeout)
        if url:
            resolved += 1
            item = item.model_copy(update={"preview_image_url": url, "image_source_type": src_type})
        enriched.append(item)

    return enriched, resolved
