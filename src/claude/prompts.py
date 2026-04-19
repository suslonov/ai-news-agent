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


def render_annotation_prompt(items: list[dict[str, Any]]) -> str:
    """Render the annotation prompt with a batch of item dicts."""
    template = load_prompt("annotate")
    items_json = json.dumps(items, indent=2, ensure_ascii=False, default=str)
    return template.replace("{{ items_json }}", items_json)
