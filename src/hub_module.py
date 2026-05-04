"""AI News module for ai-home-hub.

Implements the Module protocol expected by ai-home-hub's loader.
Can also be used standalone via src.server (prefix="").

The module handles:
  GET  /                      → serve rendered HTML
  GET  /api/unfiltered/p/N    → JSON page N of all DB rows (100 per page; preferred for fetch)
  GET  /api/unfiltered?page=N → same (query form kept for compatibility)
  POST /api/re-render         → re-render HTML from current DB state
  POST /api/mark-read         → mark item read/unread
  POST /api/save              → save/unsave item
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs

logger = logging.getLogger(__name__)

# (status, content_type, body_bytes)
Response = tuple[int, str, bytes]

# `templates/index.jinja2` indents with spaces; a plain `const API_BASE = ...` substring
# would never match and left interactive buttons broken when mounted under a path prefix.
_API_BASE_ASSIGN = re.compile(
    rb'^(\s*)const API_BASE = "[^"]*";',
    re.MULTILINE,
)

# Page index in the path avoids lost query strings on some proxies / wrappers.
_UNFILTERED_PAGE_IN_PATH = re.compile(r"/api/unfiltered/p/(\d+)$")


def _patch_served_html_api_base(content: bytes, prefix: str) -> bytes:
    """Inject `prefix` into the rendered script so fetch() targets this module's mount path."""
    assign = f'const API_BASE = "{prefix}";'.encode()

    def repl(m: re.Match[bytes]) -> bytes:
        return m.group(1) + assign

    patched, count = _API_BASE_ASSIGN.subn(repl, content, count=1)
    if count != 1:
        logger.warning(
            "Could not patch API_BASE in rendered HTML (matches=%s); "
            "check that index.jinja2 still defines const API_BASE",
            count,
        )
    return patched


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
            from src.settings import load_config, resolve_repo_path

            app_config = load_config(self.sources_yaml)
            self.db_path = resolve_repo_path(app_config.global_config.db_path, self.repo_path)
            self.output_path = resolve_repo_path(app_config.global_config.output_html, self.repo_path)
        except Exception:
            self.db_path = (self.repo_path / config.get("db_path", "data/state.db")).resolve()
            self.output_path = (self.repo_path / config.get("output_html", "data/rendered/index.html")).resolve()

    def handle(self, method: str, path: str, body: bytes, headers: dict) -> Response:
        """Route an incoming request to the appropriate handler."""
        hdrs = dict(headers)
        if "?" in path:
            path_only, _, qs = path.partition("?")
            if qs and not str(hdrs.get("X-Query-String", "")).strip():
                hdrs["X-Query-String"] = qs
            path = path_only

        route = path.rstrip("/") or "/"
        if method in ("GET", "HEAD") and route in ("", "/", "/index.html"):
            return self._serve_html()
        if method == "GET" and (route.endswith("/api/unfiltered") or _UNFILTERED_PAGE_IN_PATH.search(route)):
            return self._unfiltered_page(route, hdrs)
        if method == "POST" and path == "/api/re-render":
            return self._re_render()
        if method == "POST" and path == "/api/mark-read":
            return self._mark_read(body)
        if method == "POST" and path == "/api/save":
            return self._toggle_save(body)
        if method == "POST" and path == "/api/mark-signal":
            return self._mark_signal(body)
        if method == "POST" and path == "/api/exclude-x-account":
            return self._exclude_x_account(body)
        return 404, "text/plain", b"Not found"

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _serve_html(self) -> Response:
        try:
            content = self.output_path.read_bytes()
            # Patch API_BASE so the JS targets the correct hub-mounted prefix,
            # regardless of what api_base was used when the file was last rendered.
            content = _patch_served_html_api_base(content, self.prefix)
            return 200, "text/html; charset=utf-8", content
        except FileNotFoundError:
            return 404, "text/plain", b"No rendered HTML found. Run the pipeline first."

    def _re_render(self) -> Response:
        try:
            from src import db as database, render
            from src.settings import load_config

            config = load_config(self.sources_yaml)
            items = database.get_recent_items(self.db_path, limit=config.render.max_items_in_html)
            saved_items = database.get_saved_items(self.db_path)
            if saved_items:
                seen_ids = {item["id"] for item in items}
                items.extend(item for item in saved_items if item["id"] not in seen_ids)
            count = render.render_html(
                items=items,
                config=config.render,
                output_path=self.output_path,
                api_base=self.prefix,
                db_path=self.db_path,
                app_config=config,
                repo_root=self.repo_path,
            )
            return 200, "application/json", _json({"ok": True, "rendered": count})
        except Exception as exc:
            logger.error("Re-render failed: %s", exc, exc_info=True)
            return 500, "application/json", _json({"ok": False, "error": str(exc)})

    def _unfiltered_page(self, route: str, headers: dict) -> Response:
        """Paginated JSON of all items (read/dropped included). page is 1-based."""
        from src.db import UNFILTERED_PAGE_SIZE

        m = _UNFILTERED_PAGE_IN_PATH.search(route)
        if m:
            try:
                page = max(1, int(m.group(1)))
            except ValueError:
                page = 1
        else:
            qs = parse_qs(headers.get("X-Query-String", ""))
            try:
                page = max(1, int(qs.get("page", ["1"])[0]))
            except (TypeError, ValueError):
                page = 1
        try:
            from src import db as database
            total = database.count_all_items(self.db_path)
            offset = (page - 1) * UNFILTERED_PAGE_SIZE
            items = database.get_all_items_page(
                self.db_path, limit=UNFILTERED_PAGE_SIZE, offset=offset
            )
        except Exception as exc:
            logger.error("unfiltered page failed: %s", exc, exc_info=True)
            return 500, "application/json", _json({"ok": False, "error": str(exc)})
        payload = {
            "ok": True,
            "page": page,
            "page_size": UNFILTERED_PAGE_SIZE,
            "total": total,
            "items": items,
        }
        return 200, "application/json", _json(payload)

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

    def _exclude_x_account(self, body: bytes) -> Response:
        data = _parse_json(body)
        handle = data.get("handle", "").strip().lstrip("@")
        if not handle:
            return 400, "application/json", _json({"ok": False, "error": "missing handle"})
        try:
            from src import db as database
            database.exclude_twitter_account(self.db_path, handle)
            return 200, "application/json", _json({"ok": True, "handle": handle})
        except Exception as exc:
            logger.error("exclude-x-account failed: %s", exc)
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
