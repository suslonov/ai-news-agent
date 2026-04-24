#!/usr/bin/env bash
# Cron-ready run script for AI News Agent.
# Usage: bash scripts/run.sh [--smoke-test] [--skip-claude]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

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

/home/anton/miniconda3/envs/ai-news/bin/python -m src.main "$@" 2>&1 | tee -a "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Run complete. Output: $HOME/news_data/rendered/index.html" | tee -a "$LOG_FILE"
