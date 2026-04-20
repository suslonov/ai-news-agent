#!/usr/bin/env bash
# Build / refresh the Twitter/X account graph.
#
# Recommended schedule: weekly cron (e.g. every Sunday at 04:00).
#
# What it does:
#   1. Seeds twitter_accounts DB table from config/twitter_seeds.yaml (idempotent).
#   2. When ENABLE_X_PRODUCTION=true and X_BEARER_TOKEN is set:
#      - Expands the graph by fetching recent tweets from top accounts and
#        recording mention/retweet edges.
#      - Rescores all accounts based on appearance counts and edge weights.
#      - Prunes inactive and low-scoring discovered accounts.
#
# Usage:
#   bash scripts/build_x_graph.sh
#   bash scripts/build_x_graph.sh --dry-run   # seed only, no API calls
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

LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/build_x_graph_$(date +%Y%m%d_%H%M%S).log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting X graph build" | tee "$LOG_FILE"

python -m src.x_graph.build "$@" 2>&1 | tee -a "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] X graph build complete." | tee -a "$LOG_FILE"
