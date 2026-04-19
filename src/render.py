"""Render the static HTML output from SQLite items using Jinja2."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.models import RenderConfig

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
        return str(value)[:10]


def _from_json(value: Optional[str]) -> list:
    """Safely parse a JSON list stored as text."""
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
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


def render_html(
    items: list[dict],
    config: RenderConfig,
    output_path: Path,
    template_dir: Optional[Path] = None,
) -> int:
    """Render items to a static HTML file.

    Returns the number of items rendered.
    """
    tdir = template_dir or _TEMPLATE_DIR
    env = _build_env(tdir)
    template = env.get_template("index.jinja2")

    kept_items = [i for i in items if i.get("status") in ("kept", "candidate")]
    kept_items = kept_items[: config.max_items_in_html]

    top_stories = _pick_top_stories(kept_items, config.max_top_stories) if "top_stories" in config.sections else []
    top_ids = {i["id"] for i in top_stories}

    latest = [i for i in kept_items if i.get("id") not in top_ids]
    by_source = _group_by_source(kept_items) if "by_source" in config.sections else {}
    image_highlights = _image_highlights(kept_items) if "image_highlights" in config.sections else []

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    ctx: dict[str, Any] = {
        "top_stories": top_stories,
        "latest_items": latest,
        "by_source": by_source,
        "image_highlights": image_highlights,
        "generated_at": now,
        "total_items": len(kept_items),
    }

    html = template.render(**ctx)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("Rendered %d items to %s", len(kept_items), output_path)
    return len(kept_items)
