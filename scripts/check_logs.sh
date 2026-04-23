#!/usr/bin/env bash
# Check the latest collector run log with Claude and write a checker-log-* file.
#
# Usage:
#   bash scripts/check_logs.sh
#
# Cron example (runs every day at 07:30, 30 min after the collector):
#   30 7 * * * bash /path/to/ai-news-agent/scripts/check_logs.sh >> /tmp/checker.out 2>&1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if command -v conda &>/dev/null; then
  CONDA_ENV="${CONDA_ENV:-ai-news}"
  eval "$(conda shell.bash hook)"
  conda activate "$CONDA_ENV" 2>/dev/null || true
fi

cd "$PROJECT_ROOT"

if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

python -m src.log_checker "$@"
