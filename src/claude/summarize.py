"""Claude annotation adapter.

Sends batches of candidate items to the Anthropic API for classification,
annotation, and priority scoring. Falls back gracefully on failures.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from src.claude.prompts import render_annotation_prompt
from src.models import ClaudeAnnotation

logger = logging.getLogger(__name__)


def _parse_annotations(raw_json: str, expected_ids: list[str]) -> dict[str, ClaudeAnnotation]:
    """Parse the JSON array response from Claude into a dict keyed by item id."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.warning("Claude returned invalid JSON: %s", exc)
        logger.warning("Claude returned JSON: %s", raw_json)
        return {}

    if not isinstance(data, list):
        logger.warning("Claude returned non-list JSON: %r", type(data))
        return {}

    results: dict[str, ClaudeAnnotation] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("id", ""))
        if not item_id:
            continue
        try:
            annotation = ClaudeAnnotation(
                keep=bool(entry.get("keep", True)),
                topic=str(entry.get("topic", "Other")),
                tags=list(entry.get("tags", [])),
                annotation=str(entry.get("annotation", "")),
                why_it_matters=str(entry.get("why_it_matters", "")),
                priority_score=int(entry.get("priority_score", 0)),
            )
            results[item_id] = annotation
        except Exception as exc:
            logger.warning("Could not parse annotation for item %s: %s", item_id, exc)

    return results


def _items_to_prompt_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert DB item rows to minimal dicts for the prompt."""
    return [
        {
            "id": str(item.get("id", "")),
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "source": item.get("source_id", ""),
            "snippet": (item.get("content_snippet") or "")[:400],
        }
        for item in items
    ]


def annotate_batch(
    items: list[dict[str, Any]],
    api_key: str,
    model: str,
    max_tokens: int,
) -> dict[str, ClaudeAnnotation]:
    """Send a batch of items to Claude for annotation.

    Returns a dict mapping item id → ClaudeAnnotation.
    Falls back to an empty dict on any API error.
    """
    if not items:
        return {}

    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package is not installed. Run: pip install anthropic")
        return {}

    prompt_items = _items_to_prompt_dicts(items)
    expected_ids = [d["id"] for d in prompt_items]
    prompt = render_annotation_prompt(prompt_items)

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.error("Claude API call failed: %s", exc)
        return {}

    usage = getattr(message, "usage", None)
    logger.info(
        "Claude usage — model: %s  in: %s  out: %s  stop: %s",
        model,
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "output_tokens", "?"),
        getattr(message, "stop_reason", "?"),
    )

    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "max_tokens":
        logger.warning(
            "Claude response was truncated (stop_reason=max_tokens). "
            "Increase claude_max_tokens or reduce batch size."
        )

    text_blocks = [b for b in message.content if getattr(b, "type", None) == "text"]
    if not text_blocks:
        logger.error(
            "Claude returned no text content blocks (stop_reason=%s, blocks=%r)",
            stop_reason,
            [getattr(b, "type", b) for b in message.content],
        )
        return {}

    raw_text = text_blocks[0].text
    logger.debug("Claude raw response (%d chars): %.300s", len(raw_text), raw_text)

    # Strip accidental markdown code fences that some model versions emit
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        inner = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        raw_text = inner.strip()

    annotations = _parse_annotations(raw_text, expected_ids)
    logger.info(
        "Claude annotated %d/%d items",
        len(annotations),
        len(items),
    )
    return annotations


def apply_annotations(
    items: list[dict[str, Any]],
    annotations: dict[str, ClaudeAnnotation],
) -> list[dict[str, Any]]:
    """Merge Claude annotations into DB item dicts.

    Items without annotations retain their existing snippet as annotation fallback.
    Returns the full updated item list.
    """
    updated: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id", ""))
        ann = annotations.get(item_id)
        if ann:
            item = dict(item)
            item["topic"] = ann.topic
            item["tags_json"] = json.dumps(ann.tags)
            item["annotation"] = ann.annotation
            item["why_it_matters"] = ann.why_it_matters
            item["priority_score"] = ann.priority_score
            item["status"] = "kept" if ann.keep else "dropped"
        else:
            # Fallback: keep the item as a candidate with snippet as annotation
            if not item.get("annotation") and item.get("content_snippet"):
                item = dict(item)
                item["annotation"] = item["content_snippet"][:280]
        updated.append(item)
    return updated
