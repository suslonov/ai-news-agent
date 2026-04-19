"""Deduplication logic for collected news items.

Strategies:
1. Exact canonical URL match (after UTM stripping)
2. Content hash match (title + url)
3. Near-duplicate check using normalized title similarity
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from typing import Optional

from src.models import ItemStatus, NormalizedItem

logger = logging.getLogger(__name__)


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace for fuzzy comparison."""
    title = title.lower()
    title = unicodedata.normalize("NFKD", title)
    title = re.sub(r"[^\w\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _title_hash(title: str) -> str:
    """Stable 12-char hex hash of a normalized title for bucketing."""
    return hashlib.sha256(_normalize_title(title).encode()).hexdigest()[:12]


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _title_tokens(title: str) -> set[str]:
    """Tokenize a normalized title, filtering very short tokens."""
    return {w for w in _normalize_title(title).split() if len(w) > 2}


def deduplicate(
    items: list[NormalizedItem],
    near_dup_threshold: float = 0.75,
) -> tuple[list[NormalizedItem], list[NormalizedItem]]:
    """Deduplicate a list of NormalizedItem objects.

    Returns (kept, duplicates).

    Deduplication order:
    1. canonical_url exact match
    2. hash exact match
    3. Jaccard title similarity >= near_dup_threshold
    """
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    kept: list[NormalizedItem] = []
    duplicates: list[NormalizedItem] = []

    kept_title_tokens: list[tuple[set[str], NormalizedItem]] = []

    for item in items:
        canonical = item.canonical_url or item.url

        # 1. Exact URL match
        if canonical in seen_urls:
            logger.debug("Dropping duplicate URL: %s", canonical)
            duplicates.append(item.model_copy(update={"status": ItemStatus.duplicate}))
            continue

        # 2. Hash match
        if item.hash and item.hash in seen_hashes:
            logger.debug("Dropping duplicate hash: %s", item.hash)
            duplicates.append(item.model_copy(update={"status": ItemStatus.duplicate}))
            continue

        # 3. Near-duplicate title check
        tokens = _title_tokens(item.title)
        is_near_dup = False
        for existing_tokens, existing_item in kept_title_tokens:
            sim = _jaccard_similarity(tokens, existing_tokens)
            if sim >= near_dup_threshold:
                logger.debug(
                    "Near-duplicate (sim=%.2f): '%s' ~ '%s'",
                    sim,
                    item.title,
                    existing_item.title,
                )
                is_near_dup = True
                break

        if is_near_dup:
            duplicates.append(item.model_copy(update={"status": ItemStatus.duplicate}))
            continue

        seen_urls.add(canonical)
        if item.hash:
            seen_hashes.add(item.hash)
        kept_title_tokens.append((tokens, item))
        kept.append(item)

    logger.info("Dedupe: %d kept, %d duplicates", len(kept), len(duplicates))
    return kept, duplicates


def merge_with_db_seen(
    items: list[NormalizedItem],
    seen_urls: set[str],
    seen_hashes: set[str],
) -> tuple[list[NormalizedItem], list[NormalizedItem]]:
    """Separate items already present in the database from new candidates.

    Returns (new_items, already_seen_items).
    """
    new_items: list[NormalizedItem] = []
    already_seen: list[NormalizedItem] = []

    for item in items:
        canonical = item.canonical_url or item.url
        if canonical in seen_urls:
            already_seen.append(item)
            continue
        if item.hash and item.hash in seen_hashes:
            already_seen.append(item)
            continue
        new_items.append(item)

    return new_items, already_seen
