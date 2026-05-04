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

# Page size for archive / “Unfiltered” tab (must match hub API and render).
UNFILTERED_PAGE_SIZE = 100

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
    last_seen_at        TEXT,
    is_read             INTEGER DEFAULT 0,
    is_saved            INTEGER DEFAULT 0,
    user_signal         TEXT DEFAULT NULL,
    signal_consumed     INTEGER DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_items_canonical_url
    ON items (canonical_url)
    WHERE canonical_url IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_items_hash
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

CREATE TABLE IF NOT EXISTS twitter_accounts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    handle              TEXT NOT NULL UNIQUE,
    category            TEXT DEFAULT 'news',
    score               REAL DEFAULT 0.0,
    last_seen           TEXT,
    source              TEXT DEFAULT 'seed',
    active              INTEGER DEFAULT 1,
    added_at            TEXT NOT NULL,
    last_scored_at      TEXT,
    appearance_count    INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_twitter_accounts_active ON twitter_accounts (active);
CREATE INDEX IF NOT EXISTS idx_twitter_accounts_score  ON twitter_accounts (score DESC);

CREATE TABLE IF NOT EXISTS twitter_edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_handle     TEXT NOT NULL,
    to_handle       TEXT NOT NULL,
    edge_type       TEXT NOT NULL,
    weight          REAL DEFAULT 1.0,
    seen_count      INTEGER DEFAULT 1,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    UNIQUE (from_handle, to_handle, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_twitter_edges_to ON twitter_edges (to_handle);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply additive schema migrations for columns added after initial release."""
    migrations = [
        ("items", "is_read", "INTEGER DEFAULT 0"),
        ("items", "is_saved", "INTEGER DEFAULT 0"),
        ("items", "user_signal", "TEXT DEFAULT NULL"),
        ("items", "signal_consumed", "INTEGER DEFAULT 0"),
        ("twitter_accounts", "excluded", "INTEGER DEFAULT 0"),
    ]
    for table, column, definition in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError:
            pass  # column already exists


def init_db(db_path: Path) -> None:
    """Create all tables and indexes if they do not already exist."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)
        _run_migrations(conn)
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


def get_saved_items(db_path: Path) -> list[dict]:
    """Return all saved items ordered by most recent first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE is_saved = 1 ORDER BY published_at DESC, fetched_at DESC",
        ).fetchall()
        return [dict(r) for r in rows]


def count_all_items(db_path: Path) -> int:
    """Return total row count in items (all statuses, read/saved flags ignored)."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()
        return int(row["n"])


def get_all_items_page(db_path: Path, limit: int, offset: int) -> list[dict]:
    """Return a page of all items, newest first, regardless of status or read state."""
    if limit < 1:
        return []
    if offset < 0:
        offset = 0
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM items ORDER BY published_at DESC, fetched_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
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


def get_previous_run_started_at(db_path: Path, current_run_id: int) -> Optional[str]:
    """Return started_at of the most recent completed run before current_run_id, or None."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT started_at FROM runs WHERE id < ? ORDER BY id DESC LIMIT 1",
            (current_run_id,),
        ).fetchone()
        return row["started_at"] if row else None


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


def set_item_read(db_path: Path, item_id: int, is_read: bool = True) -> None:
    """Mark an item as read or unread."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE items SET is_read = ? WHERE id = ?",
            (1 if is_read else 0, item_id),
        )


def set_item_saved(db_path: Path, item_id: int, is_saved: bool = True) -> None:
    """Save or unsave an item."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE items SET is_saved = ? WHERE id = ?",
            (1 if is_saved else 0, item_id),
        )


def set_item_signal(db_path: Path, item_id: int, signal: Optional[str]) -> bool:
    """Set or clear a user signal on an item.

    Returns True if the update was applied, False if the item's signal has
    already been consumed by a distillation run and can no longer be changed.
    """
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE items SET user_signal = ? WHERE id = ? AND signal_consumed = 0",
            (signal, item_id),
        )
        return cursor.rowcount > 0


def mark_signals_consumed(db_path: Path, item_ids: list[int]) -> None:
    """Lock the user_signal on a batch of items after they have been distilled."""
    if not item_ids:
        return
    placeholders = ",".join("?" * len(item_ids))
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE items SET signal_consumed = 1 WHERE id IN ({placeholders})",
            item_ids,
        )


def get_items_with_signals(db_path: Path) -> list[dict]:
    """Return items that have a pending (not yet consumed) user signal."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, title, annotation, why_it_matters, topic, user_signal
               FROM items
               WHERE user_signal IS NOT NULL AND signal_consumed = 0
               ORDER BY last_seen_at DESC""",
        ).fetchall()
        return [dict(r) for r in rows]


# ── Twitter graph helpers ───────────────────────────────────────────────────────

def upsert_twitter_account(
    db_path: Path,
    handle: str,
    category: str = "news",
    source: str = "seed",
) -> None:
    """Insert a twitter account or update category/source if already present."""
    now = datetime.utcnow().isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO twitter_accounts (handle, category, source, active, added_at)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(handle) DO UPDATE SET
                   category = excluded.category,
                   source   = CASE WHEN twitter_accounts.source = 'discovered'
                                   THEN excluded.source ELSE twitter_accounts.source END,
                   active   = CASE WHEN twitter_accounts.excluded = 1 THEN 0 ELSE 1 END""",
            (handle, category, source, now),
        )


def record_twitter_edge(
    db_path: Path,
    from_handle: str,
    to_handle: str,
    edge_type: str,
) -> None:
    """Insert or bump a twitter graph edge and increment the to_handle appearance_count."""
    now = datetime.utcnow().isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO twitter_edges (from_handle, to_handle, edge_type, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(from_handle, to_handle, edge_type) DO UPDATE SET
                   seen_count = seen_count + 1,
                   weight     = weight + 0.5,
                   last_seen  = excluded.last_seen""",
            (from_handle, to_handle, edge_type, now, now),
        )
        # Bump appearance count for the discovered account.
        # Respect exclusion: never reactivate an excluded handle.
        conn.execute(
            """INSERT INTO twitter_accounts (handle, category, source, active, added_at, appearance_count)
               VALUES (?, 'news', 'discovered', 1, ?, 1)
               ON CONFLICT(handle) DO UPDATE SET
                   appearance_count = appearance_count + 1,
                   last_seen = ?,
                   active = CASE WHEN twitter_accounts.excluded = 1 THEN 0 ELSE twitter_accounts.active END""",
            (to_handle, now, now),
        )


def update_twitter_scores(db_path: Path) -> None:
    """Recompute account scores based on appearance_count and in-degree edge weight."""
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE twitter_accounts SET
                   score = (
                       appearance_count * 2.0
                       + COALESCE((
                           SELECT SUM(weight)
                           FROM twitter_edges
                           WHERE to_handle = twitter_accounts.handle
                       ), 0.0)
                   ),
                   last_scored_at = ?""",
            (datetime.utcnow().isoformat(),),
        )


def prune_twitter_accounts(
    db_path: Path,
    keep_count: int = 150,
    stale_days: int = 30,
) -> int:
    """Deactivate low-scoring or stale accounts. Returns count deactivated."""
    from datetime import timedelta
    stale_cutoff = (datetime.utcnow() - timedelta(days=stale_days)).isoformat()
    with _connect(db_path) as conn:
        # Deactivate stale accounts (not seen recently, not seed)
        conn.execute(
            """UPDATE twitter_accounts SET active = 0
               WHERE source != 'seed'
                 AND last_seen IS NOT NULL
                 AND last_seen < ?""",
            (stale_cutoff,),
        )
        # Keep only the top keep_count active accounts by score (seeds always stay)
        conn.execute(
            """UPDATE twitter_accounts SET active = 0
               WHERE active = 1
                 AND source != 'seed'
                 AND id NOT IN (
                     SELECT id FROM twitter_accounts
                     WHERE active = 1
                     ORDER BY score DESC
                     LIMIT ?
                 )""",
            (keep_count,),
        )
        row = conn.execute(
            "SELECT COUNT(*) FROM twitter_accounts WHERE active = 0"
        ).fetchone()
        return row[0]


def get_top_twitter_accounts(db_path: Path, limit: int = 20) -> list[str]:
    """Return handles of the top active, non-excluded accounts by score."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT handle FROM twitter_accounts
               WHERE active = 1 AND (excluded IS NULL OR excluded = 0)
               ORDER BY score DESC, source ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]


def exclude_twitter_account(db_path: Path, handle: str) -> None:
    """Mark an account as excluded from scanning and graph expansion.

    The account is also deactivated so it is no longer scored or expanded.
    Exclusion survives re-seeding because the excluded flag is preserved by
    the ON CONFLICT upsert in upsert_twitter_account.
    """
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE twitter_accounts SET excluded = 1, active = 0 WHERE handle = ?",
            (handle.lower().lstrip("@"),),
        )
    logger.info("Twitter account @%s excluded from scanning.", handle)


def get_all_active_twitter_handles(db_path: Path) -> list[str]:
    """Return all active twitter account handles."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT handle FROM twitter_accounts WHERE active = 1 ORDER BY score DESC"
        ).fetchall()
        return [r[0] for r in rows]


def cap_x_top_stories(
    db_path: Path,
    max_ratio: float = 0.20,
    x_source_types: frozenset[str] = frozenset(
        {"x_api_accounts", "x_api_search", "x_graph_scanner"}
    ),
) -> int:
    """Demote X/Twitter top-story items that exceed max_ratio of all top stories.

    Returns the number of items demoted.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, source_type FROM items WHERE is_top_story = 1 ORDER BY priority_score DESC"
        ).fetchall()

    total = len(rows)
    if not total:
        return 0

    max_x = max(1, int(total * max_ratio))
    x_items = [r for r in rows if r["source_type"] in x_source_types]

    if len(x_items) <= max_x:
        return 0

    # Demote the lowest-priority excess X items (list is sorted DESC so last = lowest)
    excess_ids = [r["id"] for r in x_items[max_x:]]
    placeholders = ",".join("?" * len(excess_ids))
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE items SET is_top_story = 0 WHERE id IN ({placeholders})",
            excess_ids,
        )
    return len(excess_ids)
