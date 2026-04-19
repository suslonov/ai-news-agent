"""Tests for the RSS generic collector."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import feedparser
import pytest

from src.collectors.rss_generic import (
    _compute_hash,
    _extract_image_from_entry,
    _passes_topic_filter,
    fetch_feed,
    normalize_entry,
)
from src.models import ImageSourceType, SourceConfig, SourceType, TopicFilters


def _make_source(**kwargs) -> SourceConfig:
    defaults = dict(
        id="test_rss",
        enabled=True,
        type=SourceType.rss,
        name="Test RSS",
        feed_urls=["https://example.com/feed.xml"],
        tags=["test"],
    )
    defaults.update(kwargs)
    return SourceConfig(**defaults)


def _make_entry(**kwargs) -> feedparser.FeedParserDict:
    d = feedparser.FeedParserDict(
        {
            "title": "AI Model Released",
            "link": "https://example.com/article",
            "summary": "A new AI language model was released today.",
            "author": "Jane Doe",
            "published": "Mon, 14 Apr 2025 12:00:00 GMT",
        }
    )
    d.update(kwargs)
    return d


def _all_keywords_filter() -> TopicFilters:
    return TopicFilters(include_keywords=[], exclude_keywords=[])


# ── normalize_entry ────────────────────────────────────────────────────────────


def test_normalize_entry_basic():
    source = _make_source()
    entry = _make_entry()
    item = normalize_entry(entry, source, _all_keywords_filter())
    assert item is not None
    assert item.title == "AI Model Released"
    assert item.url == "https://example.com/article"
    assert item.source_id == "test_rss"
    assert "test" in item.tags


def test_normalize_entry_missing_url_returns_none():
    source = _make_source()
    entry = _make_entry(link="")
    assert normalize_entry(entry, source, _all_keywords_filter()) is None


def test_normalize_entry_missing_title_returns_none():
    source = _make_source()
    entry = _make_entry(title="")
    assert normalize_entry(entry, source, _all_keywords_filter()) is None


def test_normalize_entry_filtered_out():
    source = _make_source()
    filters = TopicFilters(include_keywords=["blockchain"], exclude_keywords=[])
    entry = _make_entry(summary="Nothing about blockchain here except now mentioning it is unrelated")
    # The title doesn't have blockchain either
    entry2 = _make_entry(
        title="Stock Market News",
        summary="Stocks fell today. No AI.",
    )
    assert normalize_entry(entry2, source, filters) is None


def test_normalize_entry_excluded_keyword():
    source = _make_source()
    filters = TopicFilters(include_keywords=["ai"], exclude_keywords=["celebrity"])
    entry = _make_entry(
        title="AI Celebrity News",
        summary="AI tools used by celebrity today.",
    )
    assert normalize_entry(entry, source, filters) is None


def test_normalize_entry_published_at_parsed():
    source = _make_source()
    entry = _make_entry(published="Mon, 14 Apr 2025 12:00:00 +0000")
    item = normalize_entry(entry, source, _all_keywords_filter())
    assert item is not None
    assert item.published_at is not None
    assert item.published_at.year == 2025


# ── Image extraction ───────────────────────────────────────────────────────────


def test_extract_media_thumbnail():
    entry = _make_entry()
    entry["media_thumbnail"] = [{"url": "https://img.example.com/thumb.jpg"}]
    url, src = _extract_image_from_entry(entry)
    assert url == "https://img.example.com/thumb.jpg"
    assert src == ImageSourceType.media_thumbnail


def test_extract_media_content_image():
    entry = _make_entry()
    entry["media_content"] = [{"url": "https://img.example.com/img.jpg", "medium": "image"}]
    url, src = _extract_image_from_entry(entry)
    assert url == "https://img.example.com/img.jpg"
    assert src == ImageSourceType.media_content


def test_extract_enclosure_image():
    # feedparser derives 'enclosures' from the 'links' list with rel='enclosure'
    d = feedparser.FeedParserDict({"title": "AI Model Released", "link": "https://example.com/article", "summary": "Some content"})
    import feedparser.util as fpu
    dict.__setitem__(d, "links", [{"rel": "enclosure", "href": "https://img.example.com/enc.png", "type": "image/png"}])
    url, src = _extract_image_from_entry(d)
    assert url == "https://img.example.com/enc.png"
    assert src == ImageSourceType.enclosure


def test_extract_img_from_summary():
    entry = _make_entry(
        summary='<p>Some text <img src="https://img.example.com/inline.jpg"> more text</p>'
    )
    url, src = _extract_image_from_entry(entry)
    assert url == "https://img.example.com/inline.jpg"
    assert src == ImageSourceType.first_article_image


def test_no_image_returns_none():
    entry = _make_entry(summary="Plain text with no image.")
    url, src = _extract_image_from_entry(entry)
    assert url is None
    assert src == ImageSourceType.none


# ── Topic filtering ────────────────────────────────────────────────────────────


def test_passes_with_matching_keyword():
    filters = TopicFilters(include_keywords=["llm"])
    assert _passes_topic_filter("New LLM released by researchers", filters)


def test_fails_without_matching_keyword():
    filters = TopicFilters(include_keywords=["llm"])
    assert not _passes_topic_filter("Stock market update", filters)


def test_fails_with_excluded_keyword():
    filters = TopicFilters(include_keywords=["ai"], exclude_keywords=["celebrity"])
    assert not _passes_topic_filter("AI celebrity gossip", filters)


# ── Hash ──────────────────────────────────────────────────────────────────────


def test_compute_hash_stable():
    h1 = _compute_hash("Hello World", "https://example.com")
    h2 = _compute_hash("Hello World", "https://example.com")
    assert h1 == h2


def test_compute_hash_different_inputs():
    h1 = _compute_hash("Title A", "https://a.com")
    h2 = _compute_hash("Title B", "https://b.com")
    assert h1 != h2


# ── fetch_feed (mocked) ────────────────────────────────────────────────────────


def test_fetch_feed_http_error():
    """fetch_feed should return an empty parsed result on HTTP errors."""
    import httpx

    with patch("src.collectors.rss_generic.httpx.get") as mock_get:
        mock_get.side_effect = httpx.RequestError("connection refused")
        parsed = fetch_feed("https://bad.example.com/feed.xml")
    assert parsed.entries == []
