"""Medium browser-based content enricher (Playwright, optional).

Uses a persistent Playwright profile to extract full article text for
Medium items that survive initial filtering. Fails gracefully if the
session/profile is missing or Playwright is not installed.

IMPORTANT: This module never attempts to log in to Medium or bypass
access controls. It only reads pages using an already-authenticated
browser session stored in PLAYWRIGHT_USER_DATA_DIR.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from src.models import NormalizedItem

logger = logging.getLogger(__name__)


def _get_playwright_profile() -> Optional[Path]:
    from src.settings import get_playwright_user_data_dir

    return get_playwright_user_data_dir()


def enrich_item(item: NormalizedItem, timeout_ms: int = 15000) -> NormalizedItem:
    """Attempt to fetch full article text for a Medium item via Playwright.

    Returns the item unchanged on any failure.
    """
    profile = _get_playwright_profile()
    if not profile or not profile.exists():
        logger.debug(
            "Playwright profile not configured or does not exist for item %s. Skipping browser enrichment.",
            item.url,
        )
        return item

    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("Playwright is not installed. Skipping browser enrichment for %s.", item.url)
        return item

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=True,
            )
            page = browser.new_page()
            page.goto(item.url, wait_until="domcontentloaded", timeout=timeout_ms)
            # Extract readable text from the article body
            text = page.evaluate(
                """() => {
                    const el = document.querySelector('article') || document.body;
                    return el ? el.innerText : '';
                }"""
            )
            browser.close()

        if text and len(text) > 100:
            return item.model_copy(update={"full_text": text[:8000]})

    except Exception as exc:
        logger.warning("Browser enrichment failed for %s: %s", item.url, exc)

    return item


def enrich_batch(
    items: list[NormalizedItem],
    max_fetches: int = 5,
) -> list[NormalizedItem]:
    """Enrich up to max_fetches Medium items using the browser.

    Only processes items tagged with 'medium_browser_eligible'.
    Failures on individual items are caught and logged.
    """
    enriched: list[NormalizedItem] = []
    browser_fetches = 0

    for item in items:
        if "medium_browser_eligible" not in item.tags or browser_fetches >= max_fetches:
            enriched.append(item)
            continue

        browser_fetches += 1
        enriched.append(enrich_item(item))

    logger.info("Medium browser enrichment: processed %d items", browser_fetches)
    return enriched
