"""AI News Agent — log health checker.

Finds the most recent collector run log, sends it together with the full
sources.yaml to Claude for diagnosis, and writes a structured analysis to a
timestamped checker-log-* file in the configured log directory.

Run via:
    python -m src.log_checker
or:
    bash scripts/check_logs.sh
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]
    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from src.settings import get_anthropic_api_key, load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("log_checker")

_MAX_LOG_LINES = 400

_PROMPT = """\
You are a DevOps assistant reviewing an AI news aggregation pipeline run.

## Your task

1. Read the collector log below carefully.
2. Read the sources.yaml configuration.
3. Identify every error, warning, and anomaly in the log.
4. Cross-reference issues with the configuration — point out misconfigured \
sources, wrong URLs, disabled sources that should be enabled, etc.
5. Give concrete, actionable recommendations.

## Response format — use exactly these sections:

### Status
Single line: OK | WARNING | ERROR — followed by a one-sentence verdict.

### Issues found
Bullet list of distinct problems. Include the log timestamp or line excerpt \
where relevant. Write "None" if the run looks clean.

### Recommendations
Numbered list of specific fixes. For each fix state: what file, what key, \
what value to change, and why. Write "None" if no changes are needed.

---

## sources.yaml

```yaml
{sources_yaml}
```

---

## Collector run log: {log_path}
*(showing last {shown_lines} of {total_lines} lines)*

```
{log_content}
```
"""


def find_latest_run_log(log_dir: Path) -> Path | None:
    """Return the most recently modified run_*.log, or None if none exist."""
    candidates = sorted(
        log_dir.glob("run_*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def read_tail(path: Path, max_lines: int) -> tuple[str, int, int]:
    """Read the tail of *path*, capped at *max_lines*.

    Returns (content, total_line_count, shown_line_count).
    """
    lines = path.read_text(errors="replace").splitlines()
    total = len(lines)
    if total > max_lines:
        tail = [f"[... {total - max_lines} earlier lines omitted ...]"] + lines[-max_lines:]
    else:
        tail = lines
    return "\n".join(tail), total, len(tail)


def call_claude(prompt: str, api_key: str, model: str, max_tokens: int) -> str:
    """Send *prompt* to Claude and return the text response."""
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package is not installed. Run: pip install anthropic")
        return "ERROR: anthropic package not available"

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.error("Claude API call failed: %s", exc)
        return f"ERROR: Claude API call failed — {exc}"

    usage = getattr(message, "usage", None)
    logger.info(
        "Claude usage — model: %s  in: %s  out: %s  stop: %s",
        model,
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "output_tokens", "?"),
        getattr(message, "stop_reason", "?"),
    )

    if getattr(message, "stop_reason", None) == "max_tokens":
        logger.warning(
            "Claude response was truncated (stop_reason=max_tokens). "
            "Increase checker_max_tokens in sources.yaml."
        )

    text_blocks = [b for b in message.content if getattr(b, "type", None) == "text"]
    if not text_blocks:
        return "ERROR: no text in Claude response"

    text = text_blocks[0].text.strip()
    if getattr(message, "stop_reason", None) == "max_tokens":
        text += "\n\n⚠️  [TRUNCATED — increase checker_max_tokens in sources.yaml]"
    return text


def main() -> None:
    config_path = _REPO_ROOT / "config" / "sources.yaml"
    config = load_config(config_path)
    gc = config.global_config

    log_dir = Path(os.path.expanduser(gc.log_dir))
    log_dir.mkdir(parents=True, exist_ok=True)

    run_log = find_latest_run_log(log_dir)
    if not run_log:
        logger.error("No run_*.log files found in %s — run the pipeline first.", log_dir)
        sys.exit(1)

    logger.info("Checking log: %s", run_log)

    log_content, total_lines, shown_lines = read_tail(run_log, _MAX_LOG_LINES)
    sources_yaml_text = config_path.read_text()

    prompt = _PROMPT.format(
        sources_yaml=sources_yaml_text,
        log_path=run_log.name,
        shown_lines=shown_lines,
        total_lines=total_lines,
        log_content=log_content,
    )

    try:
        api_key = get_anthropic_api_key()
    except EnvironmentError as exc:
        logger.error("Cannot run checker: %s", exc)
        sys.exit(1)

    logger.info("Sending log to Claude (%s) for analysis …", gc.checker_model)
    analysis = call_claude(prompt, api_key, gc.checker_model, gc.checker_max_tokens)

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    checker_log_path = log_dir / f"checker-log-{timestamp}.log"

    header = (
        f"# AI News Agent — Log Health Check\n"
        f"# Checked at : {now.isoformat()}\n"
        f"# Run log    : {run_log}\n"
        f"# Model      : {gc.checker_model}\n"
        f"{'=' * 72}\n\n"
    )
    checker_log_path.write_text(header + analysis + "\n", encoding="utf-8")

    logger.info("Analysis written to: %s", checker_log_path)

    separator = "=" * 72
    print(f"\n{separator}\n{analysis}\n{separator}\n")


if __name__ == "__main__":
    main()
