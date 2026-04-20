#!/usr/bin/env bash
# Start the local AI News web server.
# Usage: bash scripts/serve.sh [--port 8765]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate conda environment if available
if command -v conda &>/dev/null; then
  CONDA_ENV="${CONDA_ENV:-ai-news}"
  eval "$(conda shell.bash hook)"
  conda activate "$CONDA_ENV" 2>/dev/null || true
fi

cd "$PROJECT_ROOT"

# Load .env if present
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting AI News server…"
exec python -m src.main --serve "$@"
