#!/usr/bin/env bash
# Cron-ready run script for AI News Agent.
# Usage: bash scripts/run.sh [--smoke-test] [--skip-claude]
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

LOG_DIR="$(python -c "
import os, sys
sys.path.insert(0, '$PROJECT_ROOT')
from src.settings import load_config
c = load_config()
print(os.path.expanduser(c.global_config.log_dir))
" 2>/dev/null || echo "$HOME/logs")"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run_$(date +%Y%m%d_%H%M%S).log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting AI News Agent run" | tee "$LOG_FILE"

python -m src.main "$@" 2>&1 | tee -a "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Run complete. Output: $HOME/news_data/rendered/index.html" | tee -a "$LOG_FILE"
