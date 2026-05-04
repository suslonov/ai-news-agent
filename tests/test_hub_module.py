"""Tests for NewsModule HTTP routing (unfiltered archive API)."""

from __future__ import annotations

import json
from pathlib import Path

from src.db import UNFILTERED_PAGE_SIZE, init_db, upsert_item
from src.hub_module import NewsModule
from src.models import NormalizedItem


def test_unfiltered_json_page_from_path_segment(tmp_path: Path) -> None:
    """Page index in URL path must offset results (query-only paging can lose ?page=)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    db = repo / "state.db"
    init_db(db)
    for i in range(130):
        upsert_item(
            db,
            NormalizedItem(
                source_id="s",
                source_type="rss",
                title=f"T{i}",
                url=f"https://example.com/u{i}",
            ),
        )

    mod = NewsModule(
        prefix="",
        config={"sources_yaml": "config/sources.yaml", "db_path": str(db), "output_html": str(repo / "x.html")},
        repo_path=repo,
    )
    mod.db_path = db
    mod.output_path = repo / "missing.html"

    st1, _, b1 = mod.handle("GET", "/api/unfiltered/p/1", b"", {"X-Query-String": ""})
    st2, _, b2 = mod.handle("GET", "/api/unfiltered/p/2", b"", {"X-Query-String": ""})
    assert st1 == 200 and st2 == 200
    d1 = json.loads(b1)
    d2 = json.loads(b2)
    assert d1["page"] == 1 and d2["page"] == 2
    assert len(d1["items"]) == UNFILTERED_PAGE_SIZE
    assert len(d2["items"]) == 30
    ids1 = {row["id"] for row in d1["items"]}
    ids2 = {row["id"] for row in d2["items"]}
    assert not (ids1 & ids2)
