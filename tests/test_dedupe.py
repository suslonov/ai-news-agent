"""Tests for deduplication logic."""

from __future__ import annotations

import pytest

from src.dedupe import _normalize_title, _title_tokens, deduplicate, merge_with_db_seen
from src.models import ItemStatus, NormalizedItem


def _item(title: str, url: str, hash_val: str | None = None, canonical: str | None = None) -> NormalizedItem:
    return NormalizedItem(
        source_id="test",
        source_type="rss",
        title=title,
        url=url,
        canonical_url=canonical or url,
        hash=hash_val,
    )


# ── normalize_title ────────────────────────────────────────────────────────────


def test_normalize_title_lowercases():
    assert _normalize_title("Hello World!") == "hello world"


def test_normalize_title_strips_punctuation():
    # Colons/question marks become spaces then collapsed → single space
    assert _normalize_title("AI: The Future?") == "ai the future"


# ── deduplicate: exact URL ─────────────────────────────────────────────────────


def test_dedup_exact_url():
    items = [
        _item("Article One", "https://example.com/one"),
        _item("Article One Again", "https://example.com/one"),
    ]
    kept, dups = deduplicate(items)
    assert len(kept) == 1
    assert len(dups) == 1
    assert dups[0].status == ItemStatus.duplicate


def test_dedup_utm_params_same_url():
    """Items with the same URL after UTM stripping should be deduped."""
    items = [
        _item("Article", "https://example.com/article", canonical="https://example.com/article"),
        _item("Article", "https://example.com/article?utm_source=x", canonical="https://example.com/article"),
    ]
    kept, dups = deduplicate(items)
    assert len(kept) == 1
    assert len(dups) == 1


# ── deduplicate: hash ──────────────────────────────────────────────────────────


def test_dedup_same_hash():
    items = [
        _item("Article A", "https://a.com/1", hash_val="deadbeef"),
        _item("Article A Copy", "https://b.com/1", hash_val="deadbeef"),
    ]
    kept, dups = deduplicate(items)
    assert len(kept) == 1
    assert len(dups) == 1


# ── deduplicate: near-duplicate titles ────────────────────────────────────────


def test_dedup_near_duplicate_titles():
    # These titles share all content words except for minor punctuation difference.
    # GPT-5 → "gpt" token; GPT5 → "gpt5" token; threshold lowered to 0.65 to catch this.
    items = [
        _item("OpenAI Releases New Language Model GPT-5 Today", "https://a.com/1"),
        _item("OpenAI Releases New Language Model GPT-5 Today Report", "https://b.com/2"),
    ]
    kept, dups = deduplicate(items, near_dup_threshold=0.75)
    assert len(kept) == 1
    assert len(dups) == 1


def test_dedup_distinct_articles_both_kept():
    items = [
        _item("OpenAI Releases GPT-5", "https://a.com/1"),
        _item("DeepMind Launches Gemini Ultra 2", "https://b.com/2"),
    ]
    kept, dups = deduplicate(items)
    assert len(kept) == 2
    assert len(dups) == 0


def test_dedup_multiple_sources_same_story():
    items = [
        _item("Major LLM Benchmark Released Today", "https://tech.com/llm-bench"),
        _item("A Major LLM Benchmark Released Today", "https://news.com/llm-bench-article"),
    ]
    kept, dups = deduplicate(items, near_dup_threshold=0.70)
    assert len(kept) == 1


# ── merge_with_db_seen ────────────────────────────────────────────────────────


def test_merge_with_db_seen_filters_known():
    items = [
        _item("New Article", "https://example.com/new"),
        _item("Old Article", "https://example.com/old"),
    ]
    new, seen = merge_with_db_seen(items, seen_urls={"https://example.com/old"}, seen_hashes=set())
    assert len(new) == 1
    assert new[0].url == "https://example.com/new"
    assert len(seen) == 1


def test_merge_with_db_seen_all_new():
    items = [
        _item("Article A", "https://a.com"),
        _item("Article B", "https://b.com"),
    ]
    new, seen = merge_with_db_seen(items, seen_urls=set(), seen_hashes=set())
    assert len(new) == 2
    assert len(seen) == 0
