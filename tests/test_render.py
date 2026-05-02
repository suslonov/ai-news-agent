"""Tests for HTML rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models import RenderConfig
from src.render import render_html, _fmt_date, _from_json, _pick_top_stories


def _make_item(
    id: int = 1,
    title: str = "Test Article",
    url: str = "https://example.com",
    source_id: str = "test_feed",
    status: str = "kept",
    priority_score: int = 50,
    is_top_story: int = 0,
    is_saved: int = 0,
    preview_image_url: str | None = None,
    annotation: str | None = None,
    topic: str | None = None,
    tags_json: str | None = None,
    published_at: str | None = "2025-04-14T12:00:00",
) -> dict:
    return {
        "id": id,
        "title": title,
        "url": url,
        "source_id": source_id,
        "status": status,
        "priority_score": priority_score,
        "is_top_story": is_top_story,
        "is_saved": is_saved,
        "preview_image_url": preview_image_url,
        "annotation": annotation,
        "topic": topic,
        "tags_json": tags_json or "[]",
        "published_at": published_at,
        "content_snippet": "A snippet of content.",
        "why_it_matters": None,
    }


def _make_config(**kwargs) -> RenderConfig:
    defaults = dict(
        sections=["top_stories", "latest", "by_source", "image_highlights"],
        max_top_stories=5,
        max_items_in_html=50,
        show_preview_images=True,
        keep_days=14,
        item_annotation_word_limit=70,
    )
    defaults.update(kwargs)
    return RenderConfig(**defaults)


# ── render_html ────────────────────────────────────────────────────────────────


def test_render_html_creates_file(tmp_path: Path):
    items = [_make_item(id=1), _make_item(id=2, title="Second Article", url="https://b.com")]
    output = tmp_path / "index.html"
    count = render_html(items, _make_config(), output)
    assert output.exists()
    assert count == 2


def test_render_html_contains_title(tmp_path: Path):
    items = [_make_item(title="Groundbreaking AI Research")]
    output = tmp_path / "index.html"
    render_html(items, _make_config(), output)
    html = output.read_text()
    assert "Groundbreaking AI Research" in html


def test_render_html_contains_top_story_section(tmp_path: Path):
    items = [_make_item(id=1, is_top_story=1, priority_score=90)]
    output = tmp_path / "index.html"
    render_html(items, _make_config(), output)
    html = output.read_text()
    assert "Top Stories" in html


def test_saved_items_excluded_from_top_stories_ranking(tmp_path: Path):
    """Saved/read-later bookmarks must not occupy Top Stories slots (Saved tab holds them)."""
    items = [
        _make_item(
            id=1,
            title="Saved Headline Flagged Top",
            is_top_story=1,
            priority_score=99,
            is_saved=1,
        ),
        _make_item(id=2, title="Eligible Top Fallback", priority_score=85, is_saved=0),
    ]
    output = tmp_path / "index.html"
    render_html(items, _make_config(), output)
    html = output.read_text()
    marker = '<section class="section" data-section="top_stories">'
    end_top = "</section>"
    idx = html.find(marker)
    assert idx >= 0
    after = html.find(end_top, idx + len(marker))
    assert after > idx
    top_block = html[idx:after]
    assert "Eligible Top Fallback" in top_block
    assert "Saved Headline Flagged Top" not in top_block


def test_render_html_contains_by_source_section(tmp_path: Path):
    items = [_make_item(source_id="openai_news")]
    output = tmp_path / "index.html"
    render_html(items, _make_config(), output)
    html = output.read_text()
    assert "By Source" in html


def test_render_html_contains_image(tmp_path: Path):
    items = [_make_item(preview_image_url="https://example.com/img.jpg")]
    output = tmp_path / "index.html"
    render_html(items, _make_config(), output)
    html = output.read_text()
    assert "https://example.com/img.jpg" in html
    assert "Image Highlights" in html


def test_render_html_empty_items(tmp_path: Path):
    output = tmp_path / "index.html"
    count = render_html([], _make_config(), output)
    html = output.read_text()
    assert count == 0
    assert "No items to display" in html


def test_render_html_filters_dropped(tmp_path: Path):
    items = [
        _make_item(id=1, title="Kept Article", status="kept"),
        _make_item(id=2, title="Dropped Article", status="dropped"),
    ]
    output = tmp_path / "index.html"
    count = render_html(items, _make_config(), output)
    html = output.read_text()
    assert count == 1
    assert "Kept Article" in html
    assert "Dropped Article" not in html


def test_render_html_contains_search_box(tmp_path: Path):
    output = tmp_path / "index.html"
    render_html([_make_item()], _make_config(), output)
    html = output.read_text()
    assert "search-input" in html


# ── Helper functions ───────────────────────────────────────────────────────────


def test_fmt_date_iso():
    assert _fmt_date("2025-04-14T12:00:00") == "Apr 14, 2025"


def test_fmt_date_none():
    assert _fmt_date(None) == "–"


def test_from_json_valid():
    assert _from_json('["ai", "llm"]') == ["ai", "llm"]


def test_from_json_empty():
    assert _from_json(None) == []
    assert _from_json("") == []


# ── _pick_top_stories ──────────────────────────────────────────────────────────


def test_pick_top_stories_flagged():
    items = [
        _make_item(id=1, priority_score=90, is_top_story=1),
        _make_item(id=2, priority_score=40, is_top_story=0),
    ]
    top = _pick_top_stories(items, 5)
    assert len(top) == 1
    assert top[0]["id"] == 1


def test_pick_top_stories_falls_back_to_score():
    items = [
        _make_item(id=1, priority_score=30),
        _make_item(id=2, priority_score=85),
        _make_item(id=3, priority_score=70),
    ]
    top = _pick_top_stories(items, 2)
    assert len(top) == 2
    assert top[0]["id"] == 2
