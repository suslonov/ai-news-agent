# AI News Agent

Local scheduled AI-news digest generator.

## What it does

- Collects AI-related items from configured sources
- Deduplicates repeated stories
- Uses Claude to classify, rank, and annotate kept items
- Writes a local HTML digest to `data/rendered/index.html`
- Runs incrementally via cron
- Manual feedback signals (`important` / `unrelevant`) are distilled by Claude into updated selection criteria
- Twitter/X graph builder: seeds accounts from `config/twitter_seeds.yaml`, expands via mentions/RTs, scores and prunes; graph-based scanner feeds tweets into the pipeline capped at ~20% of top stories (run `scripts/build_x_graph.sh` weekly; gated behind `ENABLE_X_PRODUCTION=true`)

## Setup with conda

```bash
conda create -y -n ai-news-agent python=3.11
conda activate ai-news-agent
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env


