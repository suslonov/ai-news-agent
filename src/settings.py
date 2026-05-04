"""Load application configuration from config/sources.yaml and environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

from src.models import AppConfig, GlobalConfig

_PROJECT_ROOT = Path(os.environ.get("AI_NEWS_AGENT_HOME", Path(__file__).parent.parent))
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "sources.yaml"


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Load and return the full application config.

    Environment variables override yaml values for global settings.
    """
    path = config_path or _DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text())

    # Rename 'global' key since it's a Python keyword and we use alias in the model
    cfg = AppConfig(**raw)

    # Apply env-var overrides to global config
    global_overrides: dict = {}
    if (v := os.environ.get("MAX_ITEMS_PER_SOURCE")):
        global_overrides["max_items_per_source"] = int(v)
    if (v := os.environ.get("MAX_CLAUDE_BATCH_ITEMS")):
        global_overrides["max_claude_batch_items"] = int(v)
    if (v := os.environ.get("MAX_FULLTEXT_FETCHES_PER_RUN")):
        global_overrides["max_fulltext_fetches_per_run"] = int(v)
    if (v := os.environ.get("ENABLE_PREVIEW_IMAGES")):
        global_overrides["enable_preview_images"] = v.lower() not in ("false", "0", "no")
    if (v := os.environ.get("ENABLE_X_PRODUCTION")):
        global_overrides["x_enabled_in_production"] = v.lower() in ("true", "1", "yes")
    if (v := os.environ.get("CLAUDE_MODEL")):
        global_overrides["claude_model"] = v
    if (v := os.environ.get("CLAUDE_MAX_TOKENS")):
        global_overrides["claude_max_tokens"] = int(v)
    if (v := os.environ.get("DISTILL_MODEL")):
        global_overrides["distill_model"] = v
    if (v := os.environ.get("DISTILL_MAX_TOKENS")):
        global_overrides["distill_max_tokens"] = int(v)

    if global_overrides:
        updated = cfg.global_config.model_copy(update=global_overrides)
        cfg = cfg.model_copy(update={"global_config": updated})

    return cfg


def get_anthropic_api_key() -> str:
    """Return the Anthropic API key from the environment."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key == "replace_me":
        raise EnvironmentError("ANTHROPIC_API_KEY is not set or is a placeholder value.")
    return key


def get_x_bearer_token() -> Optional[str]:
    """Return the X bearer token if configured."""
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    return token or None


def get_playwright_user_data_dir() -> Optional[Path]:
    """Return the Playwright persistent profile directory if configured."""
    raw = os.environ.get("PLAYWRIGHT_USER_DATA_DIR", "").strip().lstrip("=")
    return Path(raw) if raw else None


def project_root() -> Path:
    """Return the project root directory."""
    return _PROJECT_ROOT


def resolve_repo_path(path_str: str, repo_root: Path | None = None) -> Path:
    """Expand ``~`` and resolve ``path_str``. Relative paths are anchored to ``repo_root`` (default: project root)."""
    root = repo_root or _PROJECT_ROOT
    p = Path(os.path.expanduser(str(path_str).strip()))
    if p.is_absolute():
        return p.resolve()
    return (root / p).resolve()
