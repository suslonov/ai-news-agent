#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${AI_NEWS_AGENT_HOME:-$HOME/git/ai-news-agent}"
cd "$PROJECT_DIR"

if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

/home/anton/miniconda3/envs/ai-news/bin/python -m src.scheduler_entry >> "/home/anton/data/logs/run-$(date +%F).log" 2>&1


