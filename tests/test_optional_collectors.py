"""Tests for optional collectors: RSSHub, X API, X unofficial, Medium."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.models import SourceConfig, SourceType, SourceCategory, TopicFilters


def _make_source(source_type: str, **kwargs) -> SourceConfig:
    defaults = dict(
        id="test_source",
        enabled=True,
        type=source_type,
        category="optional_integrator",
        name="Test Source",
        tags=["test"],
    )
    defaults.update(kwargs)
    return SourceConfig(**defaults)


def _all_pass_filters() -> TopicFilters:
    return TopicFilters(include_keywords=[], exclude_keywords=[])


# ── RSSHub generic ────────────────────────────────────────────────────────────


def test_rsshub_no_feed_urls_returns_empty():
    from src.collectors.rsshub_generic import collect

    source = _make_source("rsshub_generic", feed_urls=[])
    result = collect(source, _all_pass_filters())
    assert result == []


def test_rsshub_delegates_to_rss_collect():
    from src.collectors.rsshub_generic import collect

    source = _make_source("rsshub_generic", feed_urls=["https://rsshub.example.com/feed"])

    with patch("src.collectors.rsshub_generic.rss_collect") as mock_rss:
        mock_rss.return_value = []
        result = collect(source, _all_pass_filters(), max_items=5)
        mock_rss.assert_called_once_with(source=source, filters=_all_pass_filters(), max_items=5)


def test_rsshub_handles_collect_exception():
    from src.collectors.rsshub_generic import collect

    source = _make_source("rsshub_generic", feed_urls=["https://bad.example.com/feed"])

    with patch("src.collectors.rsshub_generic.rss_collect", side_effect=RuntimeError("network error")):
        result = collect(source, _all_pass_filters())
    assert result == []


# ── X API ─────────────────────────────────────────────────────────────────────


def test_x_api_disabled_by_default(monkeypatch: pytest.MonkeyPatch):
    from src.collectors.x_api import collect

    monkeypatch.setenv("ENABLE_X_PRODUCTION", "false")
    source = _make_source("x_api_accounts", usernames=["OpenAI"])
    result = collect(source, _all_pass_filters())
    assert result == []


def test_x_api_missing_bearer_token_returns_empty(monkeypatch: pytest.MonkeyPatch):
    from src.collectors.x_api import collect

    monkeypatch.setenv("ENABLE_X_PRODUCTION", "true")
    monkeypatch.setenv("X_BEARER_TOKEN", "")
    source = _make_source("x_api_accounts", usernames=["OpenAI"])
    result = collect(source, _all_pass_filters())
    assert result == []


def test_x_api_normalize_tweet():
    from src.collectors.x_api import _normalize_tweet

    tweet = {
        "id": "123456789",
        "text": "New LLM model released today with 100B parameters!",
        "created_at": "2025-04-14T10:00:00Z",
        "author_id": "987",
        "_username": "OpenAI",
    }
    source = _make_source("x_api_accounts", id="x_watch", tags=["x"])
    item = _normalize_tweet(tweet, source)
    assert item is not None
    assert "OpenAI" in item.url
    assert "123456789" in item.url
    assert item.source_id == "x_watch"


def test_x_api_normalize_tweet_no_id_returns_none():
    from src.collectors.x_api import _normalize_tweet

    tweet = {"text": "Hello", "_username": "user"}
    source = _make_source("x_api_accounts", id="x_watch")
    assert _normalize_tweet(tweet, source) is None


# ── X unofficial ──────────────────────────────────────────────────────────────


def test_x_unofficial_always_returns_empty():
    from src.collectors.x_unofficial import collect

    source = _make_source("x_unofficial")
    result = collect(source, _all_pass_filters())
    assert result == []


# ── Medium RSS ────────────────────────────────────────────────────────────────


def test_medium_rss_no_feeds_returns_empty():
    from src.collectors.medium_rss import collect

    source = _make_source("medium_rss", feed_urls=[])
    result = collect(source, _all_pass_filters())
    assert result == []


def test_medium_rss_marks_browser_eligible(monkeypatch: pytest.MonkeyPatch):
    from src.collectors.medium_rss import collect
    from src.models import NormalizedItem, ImageSourceType, ItemStatus

    fake_item = NormalizedItem(
        source_id="medium_ai_topic",
        source_type="medium_rss",
        title="AI Article on Medium",
        url="https://medium.com/article",
        tags=["medium"],
    )

    with patch("src.collectors.medium_rss.rss_collect", return_value=[fake_item]):
        source = _make_source(
            "medium_rss",
            id="medium_ai_topic",
            feed_urls=["https://medium.com/feed/tag/ai"],
            enrich_with_browser_if_selected=True,
        )
        result = collect(source, _all_pass_filters())

    assert len(result) == 1
    assert "medium_browser_eligible" in result[0].tags


def test_medium_rss_no_browser_tag_without_flag(monkeypatch: pytest.MonkeyPatch):
    from src.collectors.medium_rss import collect
    from src.models import NormalizedItem

    fake_item = NormalizedItem(
        source_id="medium_ai_topic",
        source_type="medium_rss",
        title="AI Article",
        url="https://medium.com/article2",
        tags=["medium"],
    )

    with patch("src.collectors.medium_rss.rss_collect", return_value=[fake_item]):
        source = _make_source(
            "medium_rss",
            id="medium_ai_topic",
            feed_urls=["https://medium.com/feed/tag/ai"],
            enrich_with_browser_if_selected=False,
        )
        result = collect(source, _all_pass_filters())

    assert "medium_browser_eligible" not in result[0].tags


# ── Medium browser ────────────────────────────────────────────────────────────


def test_medium_browser_no_profile_returns_item_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from src.collectors.medium_browser import enrich_item
    from src.models import NormalizedItem

    monkeypatch.setenv("PLAYWRIGHT_USER_DATA_DIR", "")
    item = NormalizedItem(source_id="s", source_type="medium_rss", title="T", url="https://medium.com/x")
    result = enrich_item(item)
    assert result is item or result.url == item.url


def test_medium_browser_enrich_batch_skips_non_eligible():
    from src.collectors.medium_browser import enrich_batch
    from src.models import NormalizedItem

    item = NormalizedItem(source_id="s", source_type="medium_rss", title="T", url="https://medium.com/y", tags=[])
    result = enrich_batch([item], max_fetches=5)
    assert len(result) == 1
    assert result[0].full_text is None
