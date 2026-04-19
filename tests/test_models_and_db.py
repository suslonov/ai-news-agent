"""Tests for models, settings, and database initialization."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from src.models import (
    AppConfig,
    ClaudeAnnotation,
    GlobalConfig,
    ImageSourceType,
    ItemStatus,
    NormalizedItem,
    RunStats,
    SourceConfig,
    SourceType,
)


# ── Model tests ───────────────────────────────────────────────────────────────


def test_normalized_item_defaults():
    item = NormalizedItem(source_id="test", source_type="rss", title="Hello", url="https://example.com")
    assert item.status == ItemStatus.candidate
    assert item.image_source_type == ImageSourceType.none
    assert item.tags == []


def test_normalized_item_title_stripped():
    item = NormalizedItem(source_id="s", source_type="rss", title="  My Title  ", url="https://x.com")
    assert item.title == "My Title"


def test_normalized_item_empty_title_raises():
    with pytest.raises(Exception):
        NormalizedItem(source_id="s", source_type="rss", title="   ", url="https://x.com")


def test_claude_annotation_valid():
    ann = ClaudeAnnotation(
        keep=True,
        topic="LLM",
        tags=["llm", "openai"],
        annotation="A new model was released.",
        why_it_matters="It sets a new benchmark.",
        priority_score=80,
    )
    assert ann.keep is True
    assert ann.priority_score == 80


def test_claude_annotation_score_bounds():
    with pytest.raises(Exception):
        ClaudeAnnotation(
            keep=True, topic="t", annotation="x", why_it_matters="y", priority_score=150
        )


def test_run_stats_to_db_dict():
    stats = RunStats(fetched=10, kept=5, duplicates=2, dropped=3)
    d = stats.to_db_dict()
    assert d["fetched"] == 10
    assert d["kept"] == 5
    assert "started_at" in d


# ── Config loading tests ───────────────────────────────────────────────────────


def _make_minimal_yaml() -> dict:
    return {
        "global": {
            "timezone": "UTC",
            "db_path": "data/state.db",
            "output_html": "data/rendered/index.html",
            "max_items_per_source": 10,
            "max_fulltext_fetches_per_run": 5,
            "max_claude_batch_items": 10,
            "min_hours_between_refetch": 4,
            "enable_preview_images": True,
            "x_enabled_in_production": False,
        },
        "topic_filters": {"include_keywords": ["ai"], "exclude_keywords": []},
        "image_policy": {"resolution_order": ["og_image"], "hotlink_original_urls": True, "download_locally": False},
        "sources": [
            {
                "id": "test_feed",
                "enabled": True,
                "type": "rss",
                "category": "primary",
                "name": "Test Feed",
                "feed_urls": ["https://example.com/feed.xml"],
                "tags": ["test"],
            }
        ],
        "render": {
            "sections": ["top_stories"],
            "item_annotation_word_limit": 70,
            "keep_days": 14,
            "max_top_stories": 5,
            "max_items_in_html": 50,
            "show_preview_images": True,
        },
    }


def test_config_loading_from_yaml(tmp_path: Path):
    config_file = tmp_path / "sources.yaml"
    config_file.write_text(yaml.dump(_make_minimal_yaml()))

    from src.settings import load_config

    cfg = load_config(config_file)
    assert isinstance(cfg, AppConfig)
    assert cfg.global_config.timezone == "UTC"
    assert len(cfg.sources) == 1
    assert cfg.sources[0].id == "test_feed"
    assert cfg.sources[0].type == SourceType.rss


def test_config_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "sources.yaml"
    config_file.write_text(yaml.dump(_make_minimal_yaml()))
    monkeypatch.setenv("MAX_ITEMS_PER_SOURCE", "99")

    from src.settings import load_config

    cfg = load_config(config_file)
    assert cfg.global_config.max_items_per_source == 99


# ── Database tests ─────────────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    from src.db import init_db

    p = tmp_path / "test_state.db"
    init_db(p)
    return p


def test_init_db_creates_tables(db_path: Path):
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"items", "runs", "source_fetches"}.issubset(tables)


def test_upsert_item_inserts(db_path: Path):
    from src.db import upsert_item

    item = NormalizedItem(
        source_id="feed1",
        source_type="rss",
        title="Test Article",
        url="https://example.com/article",
    )
    row_id = upsert_item(db_path, item)
    assert row_id > 0


def test_upsert_item_deduplicates_by_url(db_path: Path):
    from src.db import upsert_item

    item = NormalizedItem(
        source_id="feed1",
        source_type="rss",
        title="Same Article",
        url="https://example.com/same",
    )
    id1 = upsert_item(db_path, item)
    id2 = upsert_item(db_path, item)
    assert id1 == id2


def test_item_exists_by_url(db_path: Path):
    from src.db import item_exists_by_url, upsert_item

    item = NormalizedItem(
        source_id="feed1", source_type="rss", title="Exists", url="https://example.com/exists"
    )
    assert not item_exists_by_url(db_path, "https://example.com/exists")
    upsert_item(db_path, item)
    assert item_exists_by_url(db_path, "https://example.com/exists")


def test_item_exists_by_hash(db_path: Path):
    from src.db import item_exists_by_hash, upsert_item

    item = NormalizedItem(
        source_id="feed1",
        source_type="rss",
        title="Hashed",
        url="https://example.com/hashed",
        hash="abc123",
    )
    assert not item_exists_by_hash(db_path, "abc123")
    upsert_item(db_path, item)
    assert item_exists_by_hash(db_path, "abc123")


def test_get_recent_items(db_path: Path):
    from src.db import get_recent_items, upsert_item

    for i in range(5):
        upsert_item(
            db_path,
            NormalizedItem(
                source_id="f",
                source_type="rss",
                title=f"Article {i}",
                url=f"https://example.com/{i}",
            ),
        )
    items = get_recent_items(db_path, limit=3)
    assert len(items) == 3


def test_mark_run_start_and_end(db_path: Path):
    from src.db import mark_run_end, mark_run_start

    run_id = mark_run_start(db_path)
    assert run_id > 0
    stats = RunStats(fetched=10, kept=5, duplicates=3, dropped=2, rendered_count=5)
    stats = stats.model_copy(update={"finished_at": datetime.utcnow()})
    mark_run_end(db_path, run_id, stats)

    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row is not None
    assert row[3] == 10  # fetched column
