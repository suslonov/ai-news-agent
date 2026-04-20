"""Cron-safe pipeline entry point.

Usage in crontab:
    0 * * * * /home/user/miniconda3/envs/ai-news/bin/python \
        /home/user/git/ai-news-agent/src/scheduler_entry.py >> /home/user/git/ai-news-agent/data/cron.log 2>&1
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

def _find_repo_root(start: Path) -> Path | None:
    current = start.resolve()
    while current != current.parent:
        if (current / "src").is_dir():
            return current
        current = current.parent
    return None

try:
    _PROJECT_ROOT = _find_repo_root(Path(__file__).resolve())
except NameError:
    _PROJECT_ROOT = _find_repo_root(Path(os.getcwd()).resolve())

try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from src import pipeline
from src.settings import load_config, project_root

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("scheduler")


def main() -> None:
    root = project_root()
    config = load_config(root / "config" / "sources.yaml")
    db_path = Path(os.path.expanduser(config.global_config.db_path))
    output_path = Path(os.path.expanduser(config.global_config.output_html))

    logger.info("Starting scheduled pipeline run …")
    stats = pipeline.run_pipeline(config, db_path, output_path)
    logger.info(
        "Finished: fetched=%d kept=%d dups=%d images=%d rendered=%d errors=%d",
        stats.fetched,
        stats.kept,
        stats.duplicates,
        stats.image_resolved_count,
        stats.rendered_count,
        len(stats.errors),
    )

    if stats.errors:
        for err in stats.errors:
            logger.warning("Source error: %s", err)


if __name__ == "__main__":
    main()
