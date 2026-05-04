"""Render the static HTML output from SQLite items using Jinja2."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.db import UNFILTERED_PAGE_SIZE, count_all_items, get_all_items_page
from src.models import AppConfig, RenderConfig
from src.settings import project_root, resolve_repo_path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


def _fmt_date(value: Optional[str]) -> str:
    """Format an ISO datetime string for display."""
    if not value:
        return "–"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except Exception:
        logger.debug("Could not parse date %r for display", value)
        return str(value)[:10]


def _from_json(value: Optional[str]) -> list:
    """Safely parse a JSON list stored as text."""
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        logger.debug("Could not parse tags JSON: %r", value)
        return []


def _build_env(template_dir: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["fmt_date"] = _fmt_date
    env.filters["from_json"] = _from_json
    return env


def _pick_top_stories(items: list[dict], max_top: int) -> list[dict]:
    """Return items marked is_top_story=1 or highest priority_score."""
    flagged = [i for i in items if i.get("is_top_story")]
    if flagged:
        return sorted(flagged, key=lambda x: x.get("priority_score", 0), reverse=True)[:max_top]
    # Fall back to highest scoring
    scored = [i for i in items if i.get("priority_score", 0) > 0]
    return sorted(scored, key=lambda x: x.get("priority_score", 0), reverse=True)[:max_top]


def _group_by_source(items: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        source = item.get("source_id", "unknown")
        groups[source].append(item)
    return dict(sorted(groups.items()))


def _image_highlights(items: list[dict], max_items: int = 18) -> list[dict]:
    return [i for i in items if i.get("preview_image_url")][:max_items]


def _group_by_topic(items: list[dict]) -> dict[str, list[dict]]:
    """Group items by their topic field; items with no topic go under 'Other'."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        topic = item.get("topic") or "Other"
        groups[topic].append(item)
    return dict(sorted(groups.items()))


def _new_since_last_run(items: list[dict], last_run_at: Optional[str]) -> list[dict]:
    """Return items whose first_seen_at is on or after last_run_at."""
    if not last_run_at:
        return []
    result: list[dict] = []
    for item in items:
        first_seen = item.get("first_seen_at")
        if first_seen and first_seen >= last_run_at:
            result.append(item)
    return result


def _reference_datetime(item: dict) -> Optional[datetime]:
    """Pick a single instant for Latest age filtering: published, else first seen, else fetched."""
    for key in ("published_at", "first_seen_at", "fetched_at"):
        raw = item.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            logger.debug("Could not parse %s=%r for item id=%s", key, raw, item.get("id"))
    return None


def _is_within_keep_days(item: dict, keep_days: int, now: datetime) -> bool:
    """Whether the item belongs in the Latest card grid (recency window)."""
    if keep_days <= 0:
        return True
    ref = _reference_datetime(item)
    if ref is None:
        return True
    cutoff = now - timedelta(days=keep_days)
    return ref >= cutoff


def _candidate_db_paths(
    db_path: Optional[Path | str],
    app_config: Optional[AppConfig],
    repo_root: Path,
) -> list[Path]:
    """Ordered unique paths to try for the archive DB (expanded, repo-relative)."""
    raw: list[str] = []
    if db_path is not None and str(db_path).strip():
        raw.append(str(db_path))
    if app_config is not None:
        raw.append(app_config.global_config.db_path)
    paths: list[Path] = []
    seen: set[str] = set()
    for s in raw:
        key = s.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        paths.append(resolve_repo_path(key, repo_root))
    return paths


def _unfiltered_from_items_fallback(items: list[dict], page_size: int) -> tuple[list[dict], int]:
    """Dedupe by id, sort by date, return first page and total (subset of in-memory items)."""
    by_id: dict[Any, dict] = {}
    for it in items:
        iid = it.get("id")
        if iid is None:
            continue
        by_id[iid] = it
    rows = list(by_id.values())
    rows.sort(
        key=lambda x: (x.get("published_at") or x.get("fetched_at") or "", str(x.get("id"))),
        reverse=True,
    )
    total = len(rows)
    return rows[:page_size], total


def render_html(
    items: list[dict],
    config: RenderConfig,
    output_path: Path,
    template_dir: Optional[Path] = None,
    last_run_at: Optional[str] = None,
    api_base: str = "",
    db_path: Optional[Path] = None,
    app_config: Optional[AppConfig] = None,
    repo_root: Optional[Path] = None,
) -> int:
    """Render items to a static HTML file.

    api_base is the URL prefix for API calls in the rendered JS, e.g. "/news"
    when mounted under ai-home-hub.  Leave empty for standalone use.

    The Unfiltered tab reads the SQLite archive using ``db_path`` and/or
    ``app_config.global_config.db_path``, resolved with ``repo_root`` (default: project root)
    so relative YAML paths work regardless of process cwd.

    Returns the number of items rendered.
    """
    tdir = template_dir or _TEMPLATE_DIR
    env = _build_env(tdir)
    template = env.get_template("index.jinja2")

    saved_items = [i for i in items if i.get("is_saved")]
    kept_items = [i for i in items if i.get("status") in ("kept", "candidate") and not i.get("is_read")]
    kept_items = kept_items[: config.max_items_in_html]

    kept_not_saved = [i for i in kept_items if not i.get("is_saved")]
    now_dt = datetime.now(timezone.utc)

    # Bookmarks (is_saved) appear only under the Saved tab, not in Recent sections.
    top_stories = (
        _pick_top_stories(kept_not_saved, config.max_top_stories) if "top_stories" in config.sections else []
    )
    top_ids = {i["id"] for i in top_stories}

    latest_timely = [
        i for i in kept_not_saved if _is_within_keep_days(i, config.keep_days, now_dt)
    ]
    latest = [i for i in latest_timely if i.get("id") not in top_ids]
    by_source = _group_by_source(kept_not_saved) if "by_source" in config.sections else {}
    by_topic = _group_by_topic(kept_not_saved) if "by_topic" in config.sections else {}
    image_highlights = (
        _image_highlights(kept_not_saved) if "image_highlights" in config.sections else []
    )
    new_since_last = (
        _new_since_last_run(kept_not_saved, last_run_at) if "new_since_last_run" in config.sections else []
    )

    has_recent_content = bool(
        top_stories or latest or by_source or by_topic or image_highlights or new_since_last
    )

    root = repo_root or project_root()
    unfiltered_initial: list[dict] = []
    unfiltered_total = 0
    from_items_fallback = False
    resolved: Optional[Path] = None

    for cand in _candidate_db_paths(db_path, app_config, root):
        if cand.exists():
            resolved = cand
            break

    if resolved is not None:
        try:
            unfiltered_total = count_all_items(resolved)
            unfiltered_initial = get_all_items_page(resolved, UNFILTERED_PAGE_SIZE, 0)
        except Exception as exc:
            logger.warning("Unfiltered archive snapshot failed: %s", exc)
            resolved = None
            unfiltered_total = 0
            unfiltered_initial = []

    if unfiltered_total == 0 and not unfiltered_initial and saved_items:
        fi, tot = _unfiltered_from_items_fallback(items, UNFILTERED_PAGE_SIZE)
        if tot > 0:
            unfiltered_initial = fi
            unfiltered_total = tot
            from_items_fallback = True
            logger.warning(
                "Unfiltered archive: no DB rows read (resolved=%s); using %d in-memory item(s) for first page. "
                "If this persists, check global.db_path and re-render.",
                resolved,
                tot,
            )

    unfiltered_boot: dict[str, Any] = {
        "page": 1,
        "page_size": UNFILTERED_PAGE_SIZE,
        "total": unfiltered_total,
        "items": unfiltered_initial,
        "has_api": bool(str(api_base).strip()),
        "from_items_fallback": from_items_fallback,
    }

    now = now_dt.strftime("%Y-%m-%d %H:%M UTC")

    ctx: dict[str, Any] = {
        "api_base": api_base,
        "top_stories": top_stories,
        "latest_items": latest,
        "by_source": by_source,
        "by_topic": by_topic,
        "image_highlights": image_highlights,
        "new_since_last_run": new_since_last,
        "saved_items": saved_items,
        "has_recent_content": has_recent_content,
        "unfiltered_initial": unfiltered_initial,
        "unfiltered_total": unfiltered_total,
        "unfiltered_boot": unfiltered_boot,
        "generated_at": now,
        "total_items": len({i["id"] for i in kept_items} | {i["id"] for i in saved_items}),
    }

    html = template.render(**ctx)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("Rendered %d items to %s", len(kept_items), output_path)
    return len(kept_items)
