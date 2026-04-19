# AI News Agent

Local scheduled AI-news digest generator.

## What it does

- Collects AI-related items from configured sources
- Deduplicates repeated stories
- Uses Claude to classify, rank, and annotate kept items
- Writes a local HTML digest to `data/rendered/index.html`
- Runs incrementally via cron

## Setup with conda

```bash
conda create -y -n ai-news-agent python=3.11
conda activate ai-news-agent
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env


