"""Distil user feedback signals into updated selection criteria.

Reads items marked 'important' or 'unrelevant' from the DB, sends them to an
LLM together with the current criteria text, and writes the result back to
config/prompts/criteria.txt so the next annotation run picks it up.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.claude.prompts import load_criteria, render_distill_prompt, save_criteria

logger = logging.getLogger(__name__)


def distill_criteria(
    items_with_signals: list[dict],
    api_key: str,
    db_path: Path,
    model: str,
    max_tokens: int,
) -> bool:
    """Run criteria distillation and persist the result.

    On success, marks every consumed item's signal as locked so it cannot be
    re-used in a future distillation run and can no longer be changed by the user.

    Args:
        items_with_signals: rows from db.get_items_with_signals()
        api_key: Anthropic API key
        db_path: path to the SQLite database (needed to mark items consumed)
        model: model slug to use for distillation
        max_tokens: token budget for the response

    Returns True if criteria were updated, False otherwise.
    """
    important = [i for i in items_with_signals if i.get("user_signal") == "important"]
    unrelevant = [i for i in items_with_signals if i.get("user_signal") == "unrelevant"]

    if not important and not unrelevant:
        logger.info("No user signals found — skipping criteria distillation.")
        return False

    logger.info(
        "Distilling criteria from %d important and %d unrelevant signals.",
        len(important),
        len(unrelevant),
    )

    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package is not installed. Run: pip install anthropic")
        return False

    current_criteria = load_criteria()
    prompt = render_distill_prompt(
        important_items=important,
        unrelevant_items=unrelevant,
        current_criteria=current_criteria,
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        new_criteria = message.content[0].text.strip()
    except Exception as exc:
        logger.error("Criteria distillation API call failed: %s", exc)
        return False

    usage = getattr(message, "usage", None)
    logger.info(
        "Claude usage — model: %s  in: %s  out: %s  stop: %s",
        model,
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "output_tokens", "?"),
        getattr(message, "stop_reason", "?"),
    )

    if not new_criteria:
        logger.warning("Distillation returned empty criteria — keeping existing.")
        return False

    save_criteria(new_criteria)
    logger.info("Selection criteria updated (%d chars).", len(new_criteria))

    # Lock consumed items — criteria have been baked in; signals must not change.
    from src import db as database
    used_ids = [int(i["id"]) for i in items_with_signals if i.get("id") is not None]
    database.mark_signals_consumed(db_path, used_ids)
    logger.info("Locked %d consumed signal(s).", len(used_ids))

    return True
