"""AI News module for ai-home-hub.

Implements the Module protocol expected by ai-home-hub's loader.
Can also be used standalone via src.server (prefix="").

The module handles:
  GET  /          → serve rendered HTML
  POST /api/re-render   → re-render HTML from current DB state
  POST /api/mark-read   → mark item read/unread
  POST /api/save        → save/unsave item
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# (status, content_type, body_bytes)
Response = tuple[int, str, bytes]


class NewsModule:
    name = "AI News"
    description = "AI-curated news aggregation"

    def __init__(self, prefix: str, config: dict, repo_path: Path) -> None:
        self.prefix = prefix
        self.repo_path = repo_path.resolve()

        self.sources_yaml = (self.repo_path / config.get("sources_yaml", "config/sources.yaml")).resolve()

        _ensure_on_path(self.repo_path)

        # Load db_path and output_html from sources.yaml so ~/... paths are honoured.
        # Fall back to hub config values only if sources.yaml is missing.
        try:
            from src.settings import load_config
            app_config = load_config(self.sources_yaml)
            import os
            self.db_path = Path(os.path.expanduser(app_config.global_config.db_path)).resolve()
            self.output_path = Path(os.path.expanduser(app_config.global_config.output_html)).resolve()
        except Exception:
            self.db_path = (self.repo_path / config.get("db_path", "data/state.db")).resolve()
            self.output_path = (self.repo_path / config.get("output_html", "data/rendered/index.html")).resolve()

    def handle(self, method: str, path: str, body: bytes, headers: dict) -> Response:
        """Route an incoming request to the appropriate handler."""
        if method in ("GET", "HEAD") and path in ("", "/", "/index.html"):
            return self._serve_html()
        if method == "POST" and path == "/api/re-render":
            return self._re_render()
        if method == "POST" and path == "/api/mark-read":
            return self._mark_read(body)
        if method == "POST" and path == "/api/save":
            return self._toggle_save(body)
        if method == "POST" and path == "/api/mark-signal":
            return self._mark_signal(body)
        return 404, "text/plain", b"Not found"

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _serve_html(self) -> Response:
        try:
            content = self.output_path.read_bytes()
            # Patch API_BASE so the JS targets the correct hub-mounted prefix,
            # regardless of what api_base was used when the file was last rendered.
            content = content.replace(
                b'const API_BASE = "";',
                f'const API_BASE = "{self.prefix}";'.encode(),
            )
            return 200, "text/html; charset=utf-8", content
        except FileNotFoundError:
            return 404, "text/plain", b"No rendered HTML found. Run the pipeline first."

    def _re_render(self) -> Response:
        try:
            from src import db as database, render
            from src.settings import load_config

            config = load_config(self.sources_yaml)
            items = database.get_recent_items(self.db_path, limit=config.render.max_items_in_html)
            count = render.render_html(
                items=items,
                config=config.render,
                output_path=self.output_path,
                api_base=self.prefix,
            )
            return 200, "application/json", _json({"ok": True, "rendered": count})
        except Exception as exc:
            logger.error("Re-render failed: %s", exc, exc_info=True)
            return 500, "application/json", _json({"ok": False, "error": str(exc)})

    def _mark_read(self, body: bytes) -> Response:
        data = _parse_json(body)
        item_id = data.get("id")
        if item_id is None:
            return 400, "application/json", _json({"ok": False, "error": "missing id"})
        try:
            from src import db as database
            database.set_item_read(self.db_path, int(item_id), bool(data.get("is_read", True)))
            return 200, "application/json", _json({"ok": True})
        except Exception as exc:
            logger.error("mark-read failed: %s", exc)
            return 500, "application/json", _json({"ok": False, "error": str(exc)})

    def _toggle_save(self, body: bytes) -> Response:
        data = _parse_json(body)
        item_id = data.get("id")
        if item_id is None:
            return 400, "application/json", _json({"ok": False, "error": "missing id"})
        try:
            from src import db as database
            database.set_item_saved(self.db_path, int(item_id), bool(data.get("is_saved", True)))
            return 200, "application/json", _json({"ok": True})
        except Exception as exc:
            logger.error("save failed: %s", exc)
            return 500, "application/json", _json({"ok": False, "error": str(exc)})

    def _mark_signal(self, body: bytes) -> Response:
        data = _parse_json(body)
        item_id = data.get("id")
        if item_id is None:
            return 400, "application/json", _json({"ok": False, "error": "missing id"})
        signal = data.get("signal")  # "important" | "unrelevant" | null
        if signal not in ("important", "unrelevant", None):
            return 400, "application/json", _json({"ok": False, "error": "signal must be 'important', 'unrelevant', or null"})
        try:
            from src import db as database
            updated = database.set_item_signal(self.db_path, int(item_id), signal)
            if not updated:
                return 409, "application/json", _json({"ok": False, "error": "signal already consumed and locked"})
            return 200, "application/json", _json({"ok": True})
        except Exception as exc:
            logger.error("mark-signal failed: %s", exc)
            return 500, "application/json", _json({"ok": False, "error": str(exc)})


# ── Helpers ────────────────────────────────────────────────────────────────────

def _json(data: dict) -> bytes:
    return json.dumps(data).encode()


def _parse_json(body: bytes) -> dict:
    try:
        return json.loads(body)
    except Exception:
        return {}


def _ensure_on_path(repo_path: Path) -> None:
    repo_str = str(repo_path)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)
