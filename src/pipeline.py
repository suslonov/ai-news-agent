"""End-to-end MVP pipeline orchestrator."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src import db, dedupe, images, render
from src.collectors import rss_generic
from src.models import AppConfig, ItemStatus, NormalizedItem, RunStats

logger = logging.getLogger(__name__)


def _collect_all_sources(config: AppConfig) -> tuple[list[NormalizedItem], list[tuple[str, str]]]:
    """Collect from all enabled sources. Failures on individual sources are caught.

    Returns (all_items, errors) where errors is a list of (source_id, message).
    """
    all_items: list[NormalizedItem] = []
    errors: list[tuple[str, str]] = []

    for source in config.sources:
        if not source.enabled:
            continue

        try:
            if source.type.value in ("rss", "medium_rss", "rsshub_generic"):
                from src.collectors import rss_generic

                items = rss_generic.collect(
                    source=source,
                    filters=config.topic_filters,
                    max_items=config.global_config.max_items_per_source,
                )
                all_items.extend(items)

            elif source.type.value == "arxiv":
                try:
                    from src.collectors import arxiv_collector

                    items = arxiv_collector.collect(
                        source=source,
                        filters=config.topic_filters,
                        max_items=config.global_config.max_items_per_source,
                    )
                    all_items.extend(items)
                except ImportError:
                    logger.debug("arxiv collector not available, skipping %s", source.id)

            elif source.type.value in ("x_api_accounts", "x_api_search"):
                if not config.global_config.x_enabled_in_production:
                    logger.debug("X collector disabled in production, skipping %s", source.id)
                    continue
                try:
                    from src.collectors import x_api

                    items = x_api.collect(source=source, filters=config.topic_filters)
                    all_items.extend(items)
                except ImportError:
                    logger.debug("X API collector not available, skipping %s", source.id)

            elif source.type.value == "x_unofficial":
                logger.debug("X unofficial collector is experimental-only, skipping %s", source.id)

            elif source.type.value == "external_reader_reference":
                logger.debug("External reader reference source %s is validation-only, skipping.", source.id)

            else:
                logger.warning("Unknown source type %s for %s, skipping.", source.type.value, source.id)

        except Exception as exc:
            msg = f"{source.id}: {exc}"
            logger.warning("Collector failed for %s: %s", source.id, exc)
            errors.append((source.id, str(exc)))

    return all_items, errors


def run_pipeline(
    config: AppConfig,
    db_path: Path,
    output_path: Path,
    skip_claude: bool = False,
) -> RunStats:
    """Run the full news aggregation pipeline.

    Steps:
    1. DB init
    2. Record run start
    3. Collect from all enabled sources
    4. Dedupe within batch
    5. Filter already-seen items from DB
    6. Enrich with page images (bounded)
    7. Persist new items
    8. Claude annotation (optional)
    9. Render HTML
    10. Record run end
    """
    stats = RunStats()
    db.init_db(db_path)
    run_id = db.mark_run_start(db_path)
    stats = stats.model_copy(update={"run_id": run_id})

    # ── 1. Collect ──────────────────────────────────────────────────────────────
    raw_items, errors = _collect_all_sources(config)
    stats = stats.model_copy(update={"fetched": len(raw_items), "errors": [e[1] for e in errors]})
    logger.info("Collected %d raw items from all sources", len(raw_items))

    # ── 2. Batch dedupe ────────────────────────────────────────────────────────
    deduped, dups = dedupe.deduplicate(raw_items)
    stats = stats.model_copy(update={"duplicates": len(dups)})

    # ── 3. Filter DB-seen items ────────────────────────────────────────────────
    existing_items = db.get_recent_items(db_path, limit=10000)
    seen_urls = {i.get("canonical_url") or i.get("url") for i in existing_items if i.get("url")}
    seen_hashes = {i["hash"] for i in existing_items if i.get("hash")}
    new_items, already_seen = dedupe.merge_with_db_seen(deduped, seen_urls, seen_hashes)
    logger.info("New items: %d, already in DB: %d", len(new_items), len(already_seen))

    # ── 4. Image enrichment ────────────────────────────────────────────────────
    if config.global_config.enable_preview_images and new_items:
        new_items, image_count = images.enrich_items_with_images(
            new_items,
            policy=config.image_policy,
            max_fetches=config.global_config.max_fulltext_fetches_per_run,
        )
        stats = stats.model_copy(update={"image_resolved_count": image_count})

    # ── 5. Persist new items ───────────────────────────────────────────────────
    for item in new_items:
        try:
            db.upsert_item(db_path, item)
        except Exception as exc:
            logger.warning("Failed to upsert item '%s': %s", item.title, exc)

    stats = stats.model_copy(update={"kept": len(new_items)})

    # ── 6. Claude annotation ───────────────────────────────────────────────────
    if not skip_claude:
        _annotate_with_claude(config, db_path, stats)

    # ── 7. Render HTML ─────────────────────────────────────────────────────────
    items_for_render = db.get_recent_items(db_path, limit=config.render.max_items_in_html)
    rendered_count = render.render_html(
        items=items_for_render,
        config=config.render,
        output_path=output_path,
    )
    stats = stats.model_copy(update={"rendered_count": rendered_count})

    # ── 8. Close run ───────────────────────────────────────────────────────────
    finished = datetime.now(timezone.utc)
    stats = stats.model_copy(update={"finished_at": finished})
    db.mark_run_end(db_path, run_id, stats)

    logger.info(
        "Run complete: fetched=%d kept=%d dups=%d images=%d rendered=%d",
        stats.fetched,
        stats.kept,
        stats.duplicates,
        stats.image_resolved_count,
        stats.rendered_count,
    )
    return stats


def _annotate_with_claude(config: AppConfig, db_path: Path, stats: RunStats) -> None:
    """Annotate candidate items with Claude and persist results."""
    try:
        from src.settings import get_anthropic_api_key

        api_key = get_anthropic_api_key()
    except EnvironmentError as exc:
        logger.warning("Skipping Claude annotation: %s", exc)
        return

    try:
        from src.claude.summarize import annotate_batch, apply_annotations
    except ImportError:
        logger.warning("Claude summarize module not available, skipping annotation.")
        return

    candidates = db.get_recent_items(db_path, limit=config.global_config.max_claude_batch_items, status="candidate")
    if not candidates:
        logger.info("No candidate items to annotate.")
        return

    annotations = annotate_batch(
        candidates,
        api_key,
        model=config.global_config.claude_model,
        max_tokens=config.global_config.claude_max_tokens,
    )
    annotated = apply_annotations(candidates, annotations)

    for item in annotated:
        try:
            import json

            db.update_item_annotation(
                db_path=db_path,
                item_id=item["id"],
                topic=item.get("topic") or "",
                tags=json.loads(item.get("tags_json") or "[]"),
                annotation=item.get("annotation") or "",
                why_it_matters=item.get("why_it_matters") or "",
                priority_score=item.get("priority_score") or 0,
                status=item.get("status") or "candidate",
                is_top_story=item.get("priority_score", 0) >= 80,
            )
        except Exception as exc:
            logger.warning("Failed to persist annotation for item %s: %s", item.get("id"), exc)
