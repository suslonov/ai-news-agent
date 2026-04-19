ai-news-agent/
├── .gitignore
├── CLAUDE.md
├── README.md
├── requirements.txt
├── .env.example
├── config/
│   ├── sources.yaml
│   └── prompts/
│       ├── summarize_batch.txt
│       ├── classify_item.txt
│       └── select_candidates.txt
├── scripts/
│   ├── run.sh
│   ├── smoke_test.sh
│   └── backup.sh
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── scheduler_entry.py
│   ├── models.py
│   ├── db.py
│   ├── settings.py
│   ├── utils.py
│   ├── dedupe.py
│   ├── ranking.py
│   ├── render.py
│   ├── pipeline.py
│   ├── extraction.py
│   ├── images.py
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── rss_generic.py
│   │   ├── arxiv.py
│   │   ├── x_api.py
│   │   ├── x_unofficial.py
│   │   ├── medium_rss.py
│   │   ├── medium_browser.py
│   │   ├── rsshub_generic.py
│   │   └── site_scraper.py
│   └── claude/
│       ├── __init__.py
│       ├── summarize.py
│       └── prompts.py
├── templates/
│   └── index.jinja2
└── tests/
    ├── test_dedupe.py
    ├── test_render.py
    ├── test_collectors.py
    ├── test_images.py
    └── test_extraction.py

~
└── news-data/
    ├── raw/
    ├── rendered/
    │   └── index.html
    ├── logs/
    └── state.db
