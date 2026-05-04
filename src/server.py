"""Standalone local server for ai-news-agent (no hub required).

The actual request logic lives in src.hub_module.NewsModule.
This wrapper runs a single-module ThreadingHTTPServer at the repo root,
mounting news at "/" so existing bookmarks and cron workflows are unchanged.

Usage:
    python -m src.main --serve [--port 8765]
    bash scripts/serve.sh
"""

from __future__ import annotations

import http.server
import logging
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from src.hub_module import NewsModule

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765


class _Handler(http.server.BaseHTTPRequestHandler):
    module: NewsModule

    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        logger.debug("HTTP %s %s", self.command, self.path)

    def do_OPTIONS(self) -> None:
        self._send(200, "text/plain", b"")

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        raw = self.path.split("?", 1)
        path = raw[0]
        query = raw[1] if len(raw) > 1 else ""
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        hdrs = dict(self.headers)
        hdrs["X-Query-String"] = query
        status, ctype, body_out = self.__class__.module.handle(method, path, body, hdrs)
        self._send(status, ctype, body_out)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)


def serve(
    config_path: Optional[Path] = None,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    """Start the standalone local server. Blocks until Ctrl-C."""
    from src.settings import project_root

    repo_path = project_root()
    sources_yaml = config_path or (repo_path / "config" / "sources.yaml")

    _Handler.module = NewsModule(
        prefix="",
        config={"sources_yaml": str(sources_yaml.relative_to(repo_path)) if sources_yaml.is_relative_to(repo_path) else str(sources_yaml)},
        repo_path=repo_path,
    )

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
