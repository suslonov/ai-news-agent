"""SQLite database initialization and helper methods."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models import NormalizedItem, RunStats

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id           TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    title               TEXT NOT NULL,
    url                 TEXT NOT NULL,
    canonical_url       TEXT,
    author              TEXT,
    published_at        TEXT,
    fetched_at          TEXT NOT NULL,
    content_snippet     TEXT,
    full_text           TEXT,
    preview_image_url   TEXT,
    image_source_type   TEXT DEFAULT 'none',
    tags_json           TEXT DEFAULT '[]',
    hash                TEXT,
    status              TEXT DEFAULT 'candidate',
    annotation          TEXT,
    why_it_matters      TEXT,
    priority_score      INTEGER DEFAULT 0,
    topic               TEXT,
    is_top_story        INTEGER DEFAULT 0,
    first_seen_at       TEXT,
    last_seen_at        TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_items_canonical_url
    ON items (canonical_url)
    WHERE canonical_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_items_hash
    ON items (hash)
    WHERE hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_items_status ON items (status);
CREATE INDEX IF NOT EXISTS idx_items_published_at ON items (published_at);

CREATE TABLE IF NOT EXISTS runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    fetched             INTEGER DEFAULT 0,
    kept                INTEGER DEFAULT 0,
    duplicates          INTEGER DEFAULT 0,
    dropped             INTEGER DEFAULT 0,
    image_resolved_count INTEGER DEFAULT 0,
    rendered_count      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS source_fetches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    source_id   TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    item_count  INTEGER DEFAULT 0,
    error       TEXT
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> None:
    """Create all tables and indexes if they do not already exist."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)
    logger.info("Database initialized at %s", db_path)


def upsert_item(db_path: Path, item: NormalizedItem) -> int:
    """Insert a new item or update an existing one matched by canonical_url or hash.

    Returns the row id.
    """
    now = datetime.utcnow().isoformat()
    tags_json = json.dumps(item.tags)
    published_at = item.published_at.isoformat() if item.published_at else None
    fetched_at = item.fetched_at.isoformat()
    canonical_url = item.canonical_url or item.url

    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM items WHERE canonical_url = ? OR (hash IS NOT NULL AND hash = ?)",
            (canonical_url, item.hash),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE items SET
                    last_seen_at = ?,
                    fetched_at = ?,
                    status = CASE WHEN status = 'candidate' THEN ? ELSE status END,
                    preview_image_url = COALESCE(preview_image_url, ?),
                    image_source_type = CASE
                        WHEN preview_image_url IS NULL THEN ? ELSE image_source_type END
                WHERE id = ?""",
                (
                    now,
                    fetched_at,
                    item.status.value,
                    item.preview_image_url,
                    item.image_source_type.value,
                    existing["id"],
                ),
            )
            return existing["id"]

        cursor = conn.execute(
            """INSERT INTO items (
                source_id, source_type, title, url, canonical_url,
                author, published_at, fetched_at, content_snippet, full_text,
                preview_image_url, image_source_type, tags_json, hash, status,
                is_top_story, first_seen_at, last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
            (
                item.source_id,
                item.source_type,
                item.title,
                item.url,
                canonical_url,
                item.author,
                published_at,
                fetched_at,
                item.content_snippet,
                item.full_text,
                item.preview_image_url,
                item.image_source_type.value,
                tags_json,
                item.hash,
                item.status.value,
                now,
                now,
            ),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_recent_items(db_path: Path, limit: int = 100, status: Optional[str] = None) -> list[dict]:
    """Return recent items ordered by published_at desc."""
    with _connect(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM items WHERE status = ? ORDER BY published_at DESC, fetched_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM items ORDER BY published_at DESC, fetched_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def mark_run_start(db_path: Path) -> int:
    """Insert a new run record and return its id."""
    now = datetime.utcnow().isoformat()
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO runs (started_at) VALUES (?)", (now,)
        )
        return cursor.lastrowid  # type: ignore[return-value]


def mark_run_end(db_path: Path, run_id: int, stats: RunStats) -> None:
    """Update the run record with final statistics."""
    data = stats.to_db_dict()
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE runs SET
                finished_at = ?,
                fetched = ?,
                kept = ?,
                duplicates = ?,
                dropped = ?,
                image_resolved_count = ?,
                rendered_count = ?
            WHERE id = ?""",
            (
                data["finished_at"] or datetime.utcnow().isoformat(),
                data["fetched"],
                data["kept"],
                data["duplicates"],
                data["dropped"],
                data["image_resolved_count"],
                data["rendered_count"],
                run_id,
            ),
        )


def log_source_fetch(
    db_path: Path,
    run_id: int,
    source_id: str,
    item_count: int,
    error: Optional[str] = None,
) -> None:
    """Record a source fetch attempt in source_fetches."""
    now = datetime.utcnow().isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO source_fetches (run_id, source_id, fetched_at, item_count, error) VALUES (?,?,?,?,?)",
            (run_id, source_id, now, item_count, error),
        )


def item_exists_by_url(db_path: Path, url: str) -> bool:
    """Return True if an item with this canonical_url or url already exists."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM items WHERE canonical_url = ? OR url = ? LIMIT 1",
            (url, url),
        ).fetchone()
        return row is not None


def item_exists_by_hash(db_path: Path, hash_val: str) -> bool:
    """Return True if an item with this content hash already exists."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM items WHERE hash = ? LIMIT 1", (hash_val,)
        ).fetchone()
        return row is not None


def update_item_annotation(
    db_path: Path,
    item_id: int,
    topic: str,
    tags: list[str],
    annotation: str,
    why_it_matters: str,
    priority_score: int,
    status: str,
    is_top_story: bool = False,
) -> None:
    """Persist Claude annotation fields onto an existing item row."""
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE items SET
                topic = ?,
                tags_json = ?,
                annotation = ?,
                why_it_matters = ?,
                priority_score = ?,
                status = ?,
                is_top_story = ?
            WHERE id = ?""",
            (
                topic,
                json.dumps(tags),
                annotation,
                why_it_matters,
                priority_score,
                status,
                1 if is_top_story else 0,
                item_id,
            ),
        )
