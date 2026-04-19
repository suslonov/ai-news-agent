"""Main CLI entry point with --smoke-test mode."""

from __future__ import annotations

import argparse
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
logger = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI News Agent pipeline")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run with 2 items per source, skip Claude, and render to a temp file.",
    )
    parser.add_argument(
        "--skip-claude",
        action="store_true",
        help="Skip Claude annotation pass.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to sources.yaml (defaults to config/sources.yaml).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = project_root()
    config_path = args.config or (root / "config" / "sources.yaml")
    config = load_config(config_path)

    db_path = Path(os.path.expanduser(config.global_config.db_path))
    output_path = Path(os.path.expanduser(config.global_config.output_html))

    if args.smoke_test:
        logger.info("=== SMOKE TEST MODE ===")
        import tempfile

        config = config.model_copy(
            update={
                "global_config": config.global_config.model_copy(
                    update={"max_items_per_source": 2, "max_fulltext_fetches_per_run": 2}
                )
            }
        )
        tmp = tempfile.mktemp(suffix=".html")
        output_path = Path(tmp)
        skip_claude = True
    else:
        skip_claude = args.skip_claude

    try:
        stats = pipeline.run_pipeline(config, db_path, output_path, skip_claude=skip_claude)
        logger.info(
            "Done: fetched=%d kept=%d dups=%d images=%d rendered=%d",
            stats.fetched,
            stats.kept,
            stats.duplicates,
            stats.image_resolved_count,
            stats.rendered_count,
        )
        if args.smoke_test:
            logger.info("Smoke test HTML written to: %s", output_path)
        return 0
    except Exception as exc:
        logger.error("Pipeline failed: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
