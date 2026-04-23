# AI News Agent

Local scheduled AI-news digest generator.

## What it does

- Collects AI-related items from configured RSS feeds, arXiv, Medium, and X/Twitter
- Deduplicates repeated stories
- Uses Claude to classify, rank, and annotate kept items
- Writes a local HTML digest (path set in `config/sources.yaml` → `global.output_html`)
- Runs incrementally via cron — SQLite keeps state between runs
- Manual feedback signals (`important` / `unrelevant`) are distilled by Claude into updated selection criteria
- X/Twitter graph builder seeds accounts from `config/twitter_seeds.yaml`, expands via mentions/RTs, scores and prunes; run separately on a weekly schedule

---

## Setup

```bash
conda create -y -n ai-news python=3.11 && conda activate ai-news
pip install -r requirements.txt
python -m playwright install chromium   # Medium browser enrichment only
cp .env.example .env                    # set ANTHROPIC_API_KEY, X_BEARER_TOKEN
```

All runtime paths (`db_path`, `output_html`, `log_dir`, API base URLs, models) are
configured in `config/sources.yaml`. No defaults are hardcoded in the application.

---

## Scripts

| Script | What it does | Typical schedule |
|---|---|---|
| `scripts/run.sh` | Full pipeline: collect → dedupe → annotate → render HTML | Daily |
| `scripts/check_logs.sh` | Send latest run log to Claude, write `checker-log-*` analysis | Daily, 30 min after run |
| `scripts/build_x_graph.sh` | Seed/expand/score/prune X account graph (separate from pipeline) | Weekly |
| `scripts/serve.sh` | Local web server to browse the digest | On demand |

```bash
bash scripts/run.sh                        # normal run
bash scripts/run.sh --smoke-test           # 2 items/source, skip Claude, temp output
bash scripts/run.sh --skip-claude          # collect and render without annotation
bash scripts/check_logs.sh
bash scripts/build_x_graph.sh             # requires X_BEARER_TOKEN
bash scripts/build_x_graph.sh --dry-run   # seed DB only, no API calls
bash scripts/serve.sh [--port 9000]       # default port 8765
```

Logs: `~/logs/run_*.log`, `~/logs/checker-log-*.log`, `~/logs/build_x_graph_*.log`
(path set by `global.log_dir` in `sources.yaml`).

## Cron

```cron
0  7 * * *   bash /path/to/ai-news-agent/scripts/run.sh         >> /tmp/ai-news.out 2>&1
30 7 * * *   bash /path/to/ai-news-agent/scripts/check_logs.sh  >> /tmp/ai-news.out 2>&1
0  4 * * 0   bash /path/to/ai-news-agent/scripts/build_x_graph.sh >> /tmp/ai-news.out 2>&1
```

Or call the scheduler entry directly (no conda needed if the env Python is used):

```cron
0 7 * * * /path/to/conda/envs/ai-news/bin/python /path/to/ai-news-agent/src/scheduler_entry.py >> ~/logs/cron.log 2>&1
```

## Python entry points

```bash
python -m src.main [--smoke-test] [--skip-claude] [--config path/to/alt.yaml]
python -m src.main --serve [--port N]
python -m src.log_checker
python -m src.x_graph.build [--dry-run] [--max-accounts N] [--max-tweets N]
python src/scheduler_entry.py
```
