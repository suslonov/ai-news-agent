# AI News Agent

## Project objective

Build and maintain a local scheduled AI-news aggregation pipeline.

The runtime must:
1. collect AI-related items from configured sources,
2. normalize them into a single schema,
3. deduplicate exact and near-duplicate stories,
4. ask Claude to classify, rank, and annotate kept items,
5. resolve a preview image URL where possible,
6. render a single local HTML file at `data/rendered/index.html`,
7. preserve state in SQLite so repeated cron runs are incremental.

This project is for personal local use.

## Hard constraints

- Python only.
- Use conda, not venv, in setup examples and scripts.
- No Docker unless explicitly requested later.
- No cloud deployment code.
- Do not build a web server. Output is a static HTML file opened locally.
- Do not mutate raw HTML in place. Always render from structured state using Jinja2.
- Use SQLite as the source of truth.
- Keep source collectors isolated so one failure does not abort the whole run.
- Implement bounded work per run:
  - source item limits,
  - full text fetch limits,
  - Claude batch size limits.
- Prefer primary sources over secondary commentary.
- Keep X/Twitter code paths implemented but disabled in production by default.
- Preserve original links, publication timestamps, and preview image URLs.
- Every collector must return normalized objects, never raw ad hoc dicts.

## Code standards

- Use type hints everywhere.
- Use Pydantic models for external and normalized data objects.
- Keep functions small and deterministic.
- Add docstrings to public functions.
- Use `pathlib`.
- Use `httpx` for HTTP.
- Use `jinja2` for HTML rendering.
- Use `tenacity` for retry wrappers around network calls only.
- No silent exception swallowing. Log warnings with enough context.
- No magic globals. Configuration must come from `config/sources.yaml` and environment variables.

## Database rules

Use SQLite in `data/state.db`.

Minimum tables:
- `items`
- `runs`
- `source_fetches`

`items` must include:
- id
- source_id
- source_type
- title
- url
- canonical_url
- author
- published_at
- fetched_at
- content_snippet
- full_text
- preview_image_url
- tags_json
- hash
- status
- annotation
- why_it_matters
- priority_score
- topic
- is_top_story
- first_seen_at
- last_seen_at

Add unique indexes on:
- canonical_url
- hash where appropriate

## Collector behavior

### RSS collectors
- Fetch feeds.
- Extract entries.
- Normalize immediately.
- Resolve preview image URLs from feed metadata when available.
- Do not fetch full article text unless the item survives initial filtering.

### Site index collectors
- Parse article list pages.
- Resolve article URLs.
- Fetch article pages only for new items.
- Extract readable text with a lightweight extractor.
- Attempt `og:image` extraction.

### X collectors
- Use official API logic in `x_api.py`.
- Keep them optional and off by default for production.
- Add `x_unofficial.py` for experimental discovery only.
- Never make production success depend on unofficial X access.

### Medium collectors
- Use RSS for discovery.
- Only use browser-based fetch for selected items after initial filtering.
- Browser fetching must use a persistent local Playwright profile.
- Never hardcode login credentials in code.

## Claude usage

Use Claude only after collection and rough filtering.

Claude tasks:
1. keep or drop candidate item
2. assign topic and tags
3. write annotation (35-70 words)
4. write a short "why it matters" clause
5. assign priority score 0-100

Claude output must be strict JSON.
If Claude fails, the pipeline must still render HTML using fallback snippets.

## Rendering rules

Render a single static file:
- `data/rendered/index.html`

The page must support:
- clean typography
- source badge
- date/time
- topic badge
- annotation
- original link
- preview image when available
- local filter/search box
- sections for top stories and latest items

No frontend framework.
Small vanilla JS only if needed for filtering.

## Preview image policy

Resolve images in this order:
1. Media RSS thumbnail/content
2. feed enclosure image
3. article `og:image`
4. first reasonable article image

Store only the image URL by default.
Do not generate images.
Do not hotlink obviously broken URLs.
If no image is found, render a text-only card.

## Free integrator policy

Optional support may exist for:
- RSSHub
- Feedly as an operator-side validation source
- Inoreader as an operator-side validation source

Do not make the production pipeline depend on third-party feed-generator uptime.
Prefer direct feeds and direct site parsing whenever possible.

## First milestone

Deliver an MVP that supports:
- RSS-based collection
- image URL extraction from feeds and pages
- SQLite persistence
- deduplication
- Claude annotation
- Jinja2 rendering
- cron-ready `scripts/run.sh`

Only after that add:
- optional RSSHub collector
- X API collector
- Medium browser collector
- experimental unofficial X fallback

