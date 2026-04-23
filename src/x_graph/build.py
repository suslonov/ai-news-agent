"""CLI entry point for the X/Twitter graph builder.

Run via:
    python -m src.x_graph.build [--dry-run]
or:
    bash scripts/build_x_graph.sh
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
logger = logging.getLogger("x_graph.build")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build / refresh the Twitter/X account graph.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Seed DB only; skip all API expansion calls regardless of ENABLE_X_PRODUCTION.",
    )
    parser.add_argument(
        "--max-accounts",
        type=int,
        default=30,
        help="Max number of accounts to expand per run (default: 30).",
    )
    parser.add_argument(
        "--max-tweets",
        type=int,
        default=50,
        help="Max tweets to fetch per account during expansion (default: 50).",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=150,
        help="Max active discovered accounts to retain after pruning (default: 150).",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=30,
        help="Deactivate discovered accounts not seen within this many days (default: 30).",
    )
    args = parser.parse_args()

    # Resolve project root and paths
    project_root = Path(os.environ.get("AI_NEWS_AGENT_HOME", Path(__file__).parent.parent.parent))
    seeds_path = project_root / "config" / "twitter_seeds.yaml"

    if not seeds_path.exists():
        logger.error("Seeds file not found: %s", seeds_path)
        sys.exit(1)

    from src.settings import load_config
    config_path = project_root / "config" / "sources.yaml"
    app_config = load_config(config_path)
    db_path = Path(os.path.expanduser(app_config.global_config.db_path))

    if args.dry_run:
        # Override env so graph.run_graph_build does not call the API
        os.environ["ENABLE_X_PRODUCTION"] = "false"
        logger.info("--dry-run: API expansion disabled, seeding only.")

    from src.x_graph.graph import run_graph_build
    summary = run_graph_build(
        db_path=db_path,
        seeds_path=seeds_path,
        api_base=app_config.global_config.x_api_base_url,
        max_accounts_to_expand=args.max_accounts,
        max_tweets_per_account=args.max_tweets,
        keep_count=args.keep,
        stale_days=args.stale_days,
    )

    logger.info(
        "Done. seeded=%d expanded=%d edges=%d pruned=%d",
        summary.get("seeded", 0),
        summary.get("expanded", 0),
        summary.get("edges_added", 0),
        summary.get("pruned", 0),
    )


if __name__ == "__main__":
    main()
