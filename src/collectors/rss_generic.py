"""Generic RSS feed collector.

Fetches feed entries, normalizes them into NormalizedItem objects,
and applies keyword-based topic filtering.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import feedparser
import httpx
from dateutil import parser as dateutil_parser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.models import ImageSourceType, ItemStatus, NormalizedItem, SourceConfig, TopicFilters

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15
_USER_AGENT = "ai-news-agent/1.0 (https://github.com/user/ai-news-agent)"


def _parse_dt(value: Optional[str | tuple]) -> Optional[datetime]:
    """Parse various date representations into a UTC-aware datetime."""
    if value is None:
        return None
    if isinstance(value, tuple):
        try:
            ts = time.mktime(value)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    try:
        dt = dateutil_parser.parse(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _extract_image_from_entry(entry: feedparser.FeedParserDict) -> tuple[Optional[str], ImageSourceType]:
    """Try to extract a preview image URL from a feedparser entry."""

    # 1. media:thumbnail
    media_thumbnail = getattr(entry, "media_thumbnail", None)
    if media_thumbnail and isinstance(media_thumbnail, list) and media_thumbnail:
        url = media_thumbnail[0].get("url")
        if url:
            return url, ImageSourceType.media_thumbnail

    # 2. media:content with medium=image
    media_content = getattr(entry, "media_content", None)
    if media_content and isinstance(media_content, list):
        for mc in media_content:
            if mc.get("medium") == "image" or mc.get("type", "").startswith("image/"):
                url = mc.get("url")
                if url:
                    return url, ImageSourceType.media_content

    # 3. enclosures
    for enclosure in entry.get("enclosures", []):
        enc_type = enclosure.get("type", "")
        if enc_type.startswith("image/"):
            url = enclosure.get("href") or enclosure.get("url")
            if url:
                return url, ImageSourceType.enclosure

    # 4. image tag inside the entry summary/content (simple scan)
    for field in ("summary", "content"):
        text = ""
        val = entry.get(field)
        if isinstance(val, list):
            text = " ".join(v.get("value", "") for v in val if isinstance(v, dict))
        elif isinstance(val, str):
            text = val
        if text:
            img_url = _scrape_first_img_src(text)
            if img_url:
                return img_url, ImageSourceType.first_article_image

    return None, ImageSourceType.none


def _scrape_first_img_src(html: str) -> Optional[str]:
    """Extract the first <img src> from an HTML fragment."""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        url = m.group(1)
        if url.startswith("http"):
            return url
    return None


def _compute_hash(title: str, url: str) -> str:
    """Compute a stable content hash from title and URL."""
    raw = f"{title.strip().lower()}|{url.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _passes_topic_filter(entry_text: str, filters: TopicFilters) -> bool:
    """Return True if the text matches include keywords and no exclude keywords."""
    lowered = entry_text.lower()
    if filters.include_keywords:
        if not any(kw.lower() in lowered for kw in filters.include_keywords):
            return False
    if filters.exclude_keywords:
        if any(kw.lower() in lowered for kw in filters.exclude_keywords):
            return False
    return True


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def _http_get_feed(url: str, timeout: int) -> str:
    """HTTP GET a feed URL with automatic retries on transport errors."""
    response = httpx.get(url, timeout=timeout, headers={"User-Agent": _USER_AGENT}, follow_redirects=True)
    response.raise_for_status()
    return response.text


def fetch_feed(
    url: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> feedparser.FeedParserDict:
    """Fetch and parse a single RSS/Atom feed URL."""
    try:
        text = _http_get_feed(url, timeout)
        return feedparser.parse(text)
    except httpx.HTTPStatusError as exc:
        logger.warning("HTTP error fetching feed %s: %s", url, exc)
        return feedparser.parse("")
    except Exception as exc:
        logger.warning("Unexpected error fetching feed %s: %s", url, exc)
        return feedparser.parse("")


def normalize_entry(
    entry: feedparser.FeedParserDict,
    source: SourceConfig,
    filters: TopicFilters,
    max_snippet_chars: int = 500,
) -> Optional[NormalizedItem]:
    """Normalize a single feedparser entry into a NormalizedItem.

    Returns None if the entry is filtered out or lacks a URL/title.
    """
    url = entry.get("link", "").strip()
    title = entry.get("title", "").strip()
    if not url or not title:
        return None

    # Build content snippet for filtering
    summary = entry.get("summary", "") or ""
    if isinstance(summary, list):
        summary = " ".join(s.get("value", "") for s in summary if isinstance(s, dict))
    snippet = summary[:max_snippet_chars].strip()

    filter_text = f"{title} {snippet}"
    if not _passes_topic_filter(filter_text, filters):
        return None

    author = entry.get("author", "") or entry.get("author_detail", {}).get("name", "")
    published_at = _parse_dt(entry.get("published") or entry.get("updated") or entry.get("published_parsed"))
    image_url, image_source = _extract_image_from_entry(entry)
    content_hash = _compute_hash(title, url)

    return NormalizedItem(
        source_id=source.id,
        source_type=source.type.value,
        title=title,
        url=url,
        canonical_url=url,
        author=author or None,
        published_at=published_at,
        content_snippet=snippet or None,
        preview_image_url=image_url,
        image_source_type=image_source,
        tags=list(source.tags),
        hash=content_hash,
        status=ItemStatus.candidate,
    )


def collect(
    source: SourceConfig,
    filters: TopicFilters,
    max_items: int = 20,
) -> list[NormalizedItem]:
    """Collect and normalize items from all feed URLs of a source.

    Failures on individual feed URLs are logged and skipped.
    """
    results: list[NormalizedItem] = []

    for feed_url in source.feed_urls:
        logger.info("Fetching RSS feed: %s", feed_url)
        parsed = fetch_feed(feed_url)

        if parsed.bozo and not parsed.entries:
            logger.warning("Feed parse error for %s: %s", feed_url, parsed.get("bozo_exception"))
            continue

        for entry in parsed.entries:
            if len(results) >= max_items:
                break
            item = normalize_entry(entry, source, filters)
            if item:
                results.append(item)

        if len(results) >= max_items:
            break

    logger.info("Source %s: collected %d items", source.id, len(results))
    return results
