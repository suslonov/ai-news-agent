"""End-to-end MVP pipeline orchestrator."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
import time
from pathlib import Path
from typing import Optional

from src import db, dedupe, images, render
from src.collectors import arxiv, medium_browser, medium_rss, rss_generic, rsshub_generic, x_api
from src.models import AppConfig, NormalizedItem, RunStats
from src.claude.summarize import annotate_batch, apply_annotations
from src.claude.distill import distill_criteria
from src.settings import get_anthropic_api_key
from src.x_graph import scanner as x_graph_scanner

logger = logging.getLogger(__name__)


def _collect_all_sources(
    config: AppConfig,
    db_path: Path,
    run_id: int,
) -> tuple[list[NormalizedItem], list[tuple[str, str]]]:
    """Collect from all enabled sources. Failures on individual sources are caught.

    Returns (all_items, errors) where errors is a list of (source_id, message).
    """
    all_items: list[NormalizedItem] = []
    errors: list[tuple[str, str]] = []

    for source in config.sources:
        if not source.enabled:
            continue

        items: list[NormalizedItem] = []
        error_msg: Optional[str] = None

        try:
            gc = config.global_config

            if source.type.value == "rss":
                items = rss_generic.collect(
                    source=source,
                    filters=config.topic_filters,
                    user_agent=gc.user_agent,
                    max_items=gc.max_items_per_source,
                )

            elif source.type.value == "medium_rss":
                items = medium_rss.collect(
                    source=source,
                    filters=config.topic_filters,
                    user_agent=gc.user_agent,
                    max_items=gc.max_items_per_source,
                )

            elif source.type.value == "rsshub_generic":
                items = rsshub_generic.collect(
                    source=source,
                    filters=config.topic_filters,
                    user_agent=gc.user_agent,
                    max_items=gc.max_items_per_source,
                )

            elif source.type.value == "arxiv":
                items = arxiv.collect(
                    source=source,
                    filters=config.topic_filters,
                    api_base=gc.arxiv_api_base_url,
                    user_agent=gc.user_agent,
                    max_items=gc.max_items_per_source,
                )

            elif source.type.value in ("x_api_accounts", "x_api_search"):
                if not gc.x_enabled_in_production:
                    logger.debug("X collector disabled in production, skipping %s", source.id)
                    continue

                items = x_api.collect(source=source, filters=config.topic_filters, global_config=gc)
                time.sleep(10)


            elif source.type.value == "x_graph_scanner":
                if not gc.x_enabled_in_production:
                    logger.debug("X graph scanner disabled (ENABLE_X_PRODUCTION=false), skipping %s", source.id)
                    continue

                items = x_graph_scanner.collect(
                    source=source,
                    filters=config.topic_filters,
                    db_path=db_path,
                    global_config=gc,
                    max_accounts=gc.graph_accounts_to_scan,
                    max_items=gc.max_items_per_source,
                )

            elif source.type.value == "x_unofficial":
                logger.debug("X unofficial collector is experimental-only, skipping %s", source.id)

            elif source.type.value == "external_reader_reference":
                logger.debug("External reader reference source %s is validation-only, skipping.", source.id)

            else:
                logger.warning("Unknown source type %s for %s, skipping.", source.type.value, source.id)

        except Exception as exc:
            error_msg = str(exc)
            logger.warning("Collector failed for %s: %s", source.id, exc)
            errors.append((source.id, error_msg))

        all_items.extend(items)
        
        db.log_source_fetch(db_path, run_id, source.id, len(items), error_msg)

    return all_items, errors


def run_pipeline(
    config: AppConfig,
    db_path: Path,
    output_path: Path,
    skip_claude: bool = False,
) -> RunStats:
    """Run the full news aggregation pipeline.

    Steps:
    1. DB init + record run start
    2. Collect from all enabled sources (logs source_fetches)
    3. Dedupe within batch
    4. Filter already-seen items from DB
    5. Medium browser enrichment (bounded)
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
    last_run_at = db.get_previous_run_started_at(db_path, run_id)

    # ── 1. Collect ──────────────────────────────────────────────────────────────
    raw_items, errors = _collect_all_sources(config, db_path, run_id)
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

    # ── 4. Medium browser enrichment ──────────────────────────────────────────
    new_items = medium_browser.enrich_batch(
        new_items,
        max_fetches=config.global_config.max_fulltext_fetches_per_run,
    )

    # ── 5. Image enrichment ────────────────────────────────────────────────────
    if config.global_config.enable_preview_images and new_items:
        new_items, image_count = images.enrich_items_with_images(
            new_items,
            policy=config.image_policy,
            user_agent=config.global_config.user_agent,
            max_fetches=config.global_config.max_fulltext_fetches_per_run,
        )
        stats = stats.model_copy(update={"image_resolved_count": image_count})

    # ── 6. Persist new items ───────────────────────────────────────────────────
    for item in new_items:
        try:
            db.upsert_item(db_path, item)
        except Exception as exc:
            logger.warning("Failed to upsert item '%s': %s", item.title, exc)

    stats = stats.model_copy(update={"kept": len(new_items)})

    # ── 7. Distil user feedback → updated selection criteria ───────────────────
    if not skip_claude:
        _distill_criteria_from_signals(config, db_path)

    # ── 8. Claude annotation ───────────────────────────────────────────────────
    if not skip_claude:
        _annotate_with_claude(config, db_path, stats)
        _enforce_x_top_story_cap(db_path, config.global_config.x_top_story_max_ratio)

    # ── 9. Render HTML ─────────────────────────────────────────────────────────
    items_for_render = db.get_recent_items(db_path, limit=config.render.max_items_in_html)
    saved_items_for_render = db.get_saved_items(db_path)
    if saved_items_for_render:
        seen_ids = {item["id"] for item in items_for_render}
        items_for_render.extend(item for item in saved_items_for_render if item["id"] not in seen_ids)
    rendered_count = render.render_html(
        items=items_for_render,
        config=config.render,
        output_path=output_path,
        last_run_at=last_run_at,
    )
    stats = stats.model_copy(update={"rendered_count": rendered_count})

    # ── 10. Close run ──────────────────────────────────────────────────────────
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


def _distill_criteria_from_signals(config: AppConfig, db_path: Path) -> None:
    """Update selection criteria by distilling user-signal annotations via LLM."""
    try:
        api_key = get_anthropic_api_key()
    except EnvironmentError as exc:
        logger.warning("Skipping criteria distillation: %s", exc)
        return

    items = db.get_items_with_signals(db_path)
    if not items:
        return

    distill_criteria(
        items_with_signals=items,
        api_key=api_key,
        db_path=db_path,
        model=config.global_config.distill_model,
        max_tokens=config.global_config.distill_max_tokens,
    )


def _enforce_x_top_story_cap(db_path: Path, max_ratio: float) -> None:
    """Demote excess X/Twitter top stories to stay within the configured ratio."""
    demoted = db.cap_x_top_stories(db_path, max_ratio=max_ratio)
    if demoted:
        logger.info(
            "X top-story cap (%.0f%%): demoted %d tweet(s) from top stories",
            max_ratio * 100,
            demoted,
        )


def _annotate_with_claude(config: AppConfig, db_path: Path, stats: RunStats) -> None:
    """Annotate candidate items with Claude and persist results."""
    try:
        api_key = get_anthropic_api_key()
    except EnvironmentError as exc:
        logger.warning("Skipping Claude annotation: %s", exc)
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
