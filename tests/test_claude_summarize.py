"""Tests for Claude annotation parsing and fallback behavior."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.claude.summarize import _items_to_prompt_dicts, _parse_annotations, apply_annotations
from src.models import ClaudeAnnotation


def _annotation_dict(**kwargs) -> dict:
    defaults = dict(
        id="1",
        keep=True,
        topic="LLM",
        tags=["llm", "openai"],
        annotation="A useful annotation about the item.",
        why_it_matters="It advances the state of the art.",
        priority_score=75,
    )
    defaults.update(kwargs)
    return defaults


# ── _parse_annotations ─────────────────────────────────────────────────────────


def test_parse_annotations_valid():
    raw = json.dumps([_annotation_dict(id="42")])
    result = _parse_annotations(raw, ["42"])
    assert "42" in result
    ann = result["42"]
    assert isinstance(ann, ClaudeAnnotation)
    assert ann.keep is True
    assert ann.topic == "LLM"
    assert ann.priority_score == 75


def test_parse_annotations_invalid_json():
    result = _parse_annotations("not json at all", ["1"])
    assert result == {}


def test_parse_annotations_not_list():
    result = _parse_annotations('{"key": "value"}', ["1"])
    assert result == {}


def test_parse_annotations_missing_id_skipped():
    raw = json.dumps([{"keep": True, "topic": "LLM", "annotation": "x", "why_it_matters": "y", "priority_score": 50}])
    result = _parse_annotations(raw, ["1"])
    assert result == {}


def test_parse_annotations_invalid_score_skipped():
    raw = json.dumps([_annotation_dict(id="1", priority_score=200)])
    result = _parse_annotations(raw, ["1"])
    assert "1" not in result


def test_parse_annotations_partial_batch():
    raw = json.dumps([
        _annotation_dict(id="1"),
        _annotation_dict(id="3", topic="Research"),
    ])
    result = _parse_annotations(raw, ["1", "2", "3"])
    assert "1" in result
    assert "2" not in result
    assert "3" in result
    assert result["3"].topic == "Research"


# ── apply_annotations ─────────────────────────────────────────────────────────


def test_apply_annotations_merges_fields():
    items = [{"id": 1, "title": "Test", "status": "candidate", "content_snippet": "snip"}]
    annotations = {
        "1": ClaudeAnnotation(
            keep=True,
            topic="Agents",
            tags=["agents"],
            annotation="Full annotation.",
            why_it_matters="Matters because.",
            priority_score=80,
        )
    }
    result = apply_annotations(items, annotations)
    assert result[0]["topic"] == "Agents"
    assert result[0]["status"] == "kept"
    assert result[0]["priority_score"] == 80


def test_apply_annotations_drop_sets_status():
    items = [{"id": 1, "title": "Low quality", "status": "candidate", "content_snippet": ""}]
    annotations = {
        "1": ClaudeAnnotation(
            keep=False,
            topic="Other",
            tags=[],
            annotation="Not relevant.",
            why_it_matters="Not significant.",
            priority_score=10,
        )
    }
    result = apply_annotations(items, annotations)
    assert result[0]["status"] == "dropped"


def test_apply_annotations_fallback_snippet():
    """Items without Claude annotation should fall back to content_snippet as annotation."""
    items = [{"id": 2, "title": "Missing", "status": "candidate", "content_snippet": "A fallback snippet."}]
    result = apply_annotations(items, {})
    assert result[0]["annotation"] == "A fallback snippet."


def test_apply_annotations_no_annotation_no_snippet():
    items = [{"id": 3, "title": "Empty", "status": "candidate", "content_snippet": None}]
    result = apply_annotations(items, {})
    assert result[0].get("annotation") is None


# ── _items_to_prompt_dicts ────────────────────────────────────────────────────


def test_items_to_prompt_dicts_maps_fields():
    items = [{"id": 5, "title": "My Title", "url": "https://x.com", "source_id": "feed1", "content_snippet": "Some text here."}]
    result = _items_to_prompt_dicts(items)
    assert len(result) == 1
    assert result[0]["id"] == "5"
    assert result[0]["title"] == "My Title"
    assert result[0]["snippet"] == "Some text here."
