"""Minimal local HTTP server for the AI News web UI.

Usage:
    python -m src.main --serve
    # or directly:
    python -m src.server
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import threading
import webbrowser
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765


class _Handler(http.server.BaseHTTPRequestHandler):
    db_path: Path
    output_path: Path
    config_path: Optional[Path] = None

    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        logger.debug("HTTP %s %s", self.command, self.path)

    def do_OPTIONS(self) -> None:
        self._cors_headers(200)
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._serve_html()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/api/re-render":
            self._re_render()
        elif self.path == "/api/mark-read":
            self._mark_read()
        elif self.path == "/api/save":
            self._toggle_save()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_html(self) -> None:
        try:
            content = self.__class__.output_path.read_bytes()
        except FileNotFoundError:
            msg = b"No rendered HTML found. Run the pipeline first."
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _re_render(self) -> None:
        try:
            from src import db as database, render
            from src.settings import load_config, project_root

            root = project_root()
            cfg_path = self.__class__.config_path or (root / "config" / "sources.yaml")
            config = load_config(cfg_path)
            db_path = self.__class__.db_path
            output_path = self.__class__.output_path

            items = database.get_recent_items(db_path, limit=config.render.max_items_in_html)
            count = render.render_html(items=items, config=config.render, output_path=output_path)
            self._json_response({"ok": True, "rendered": count})
        except Exception as exc:
            logger.error("Re-render failed: %s", exc, exc_info=True)
            self._json_response({"ok": False, "error": str(exc)}, status=500)

    def _mark_read(self) -> None:
        body = self._read_body()
        item_id = body.get("id")
        if item_id is None:
            self._json_response({"ok": False, "error": "missing id"}, status=400)
            return
        is_read = bool(body.get("is_read", True))
        try:
            from src import db as database
            database.set_item_read(self.__class__.db_path, int(item_id), is_read)
            self._json_response({"ok": True})
        except Exception as exc:
            logger.error("mark-read failed: %s", exc)
            self._json_response({"ok": False, "error": str(exc)}, status=500)

    def _toggle_save(self) -> None:
        body = self._read_body()
        item_id = body.get("id")
        if item_id is None:
            self._json_response({"ok": False, "error": "missing id"}, status=400)
            return
        is_saved = bool(body.get("is_saved", True))
        try:
            from src import db as database
            database.set_item_saved(self.__class__.db_path, int(item_id), is_saved)
            self._json_response({"ok": True})
        except Exception as exc:
            logger.error("save failed: %s", exc)
            self._json_response({"ok": False, "error": str(exc)}, status=500)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _cors_headers(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data).encode()
        self._cors_headers(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve(
    db_path: Path,
    output_path: Path,
    config_path: Optional[Path] = None,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    """Start the local HTTP server. Blocks until Ctrl-C."""
    _Handler.db_path = db_path
    _Handler.output_path = output_path
    _Handler.config_path = config_path

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://localhost:{port}/"
    logger.info("AI News server running at %s — Ctrl-C to stop", url)

    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    from src.settings import load_config, project_root

    root = project_root()
    cfg = load_config(root / "config" / "sources.yaml")
    _db = Path(os.path.expanduser(cfg.global_config.db_path))
    _out = Path(os.path.expanduser(cfg.global_config.output_html))
    serve(_db, _out, config_path=root / "config" / "sources.yaml")
