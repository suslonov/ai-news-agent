"""Load and render prompt templates from config/prompts/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PROMPT_DIR = Path(__file__).parent.parent.parent / "config" / "prompts"


def load_prompt(name: str) -> str:
    """Load a prompt template by name (without extension)."""
    path = _PROMPT_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def load_criteria() -> str:
    """Load the current selection criteria text, returning empty string if missing."""
    path = _PROMPT_DIR / "criteria.txt"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def save_criteria(text: str) -> None:
    """Persist updated selection criteria to disk."""
    path = _PROMPT_DIR / "criteria.txt"
    path.write_text(text.strip() + "\n", encoding="utf-8")


def render_annotation_prompt(items: list[dict[str, Any]]) -> str:
    """Render the annotation prompt with a batch of item dicts and current criteria."""
    template = load_prompt("annotate")
    criteria = load_criteria()
    items_json = json.dumps(items, indent=2, ensure_ascii=False, default=str)
    prompt = template.replace("{{ criteria }}", criteria)
    return prompt.replace("{{ items_json }}", items_json)


def render_distill_prompt(
    important_items: list[dict[str, Any]],
    unrelevant_items: list[dict[str, Any]],
    current_criteria: str,
) -> str:
    """Render the criteria-distillation prompt."""
    template = load_prompt("distill_criteria")

    def _fmt(items: list[dict[str, Any]]) -> str:
        if not items:
            return "(none)"
        lines = []
        for item in items:
            title = item.get("title", "")
            annotation = item.get("annotation") or item.get("why_it_matters") or ""
            topic = item.get("topic") or ""
            parts = [f"- {title}"]
            if topic:
                parts[0] += f" [{topic}]"
            if annotation:
                parts.append(f"  {annotation[:200]}")
            lines.append("\n".join(parts))
        return "\n".join(lines)

    prompt = template.replace("{{ current_criteria }}", current_criteria or "(none yet)")
    prompt = prompt.replace("{{ important_items }}", _fmt(important_items))
    prompt = prompt.replace("{{ unrelevant_items }}", _fmt(unrelevant_items))
    return prompt
