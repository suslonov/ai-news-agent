
## Revised implementation prompts for Cursor

### 1. — bootstrap models, DB, config loader, image fields

Read CLAUDE.md and implement the first MVP layer only.

1. create src/models.py with Pydantic models for:
   - SourceConfig
   - GlobalConfig
   - NormalizedItem
   - ClaudeAnnotation
   - RunStats

2. ensure NormalizedItem includes:
   - preview_image_url
- image_source_type

3. create src/settings.py to load config/sources.yaml and environment variables

4. create src/db.py with SQLite initialization and helper methods:
   - init_db()
   - upsert_item()
   - get_recent_items(limit)
   - mark_run_start()
   - mark_run_end()
   - item_exists_by_url()
   - item_exists_by_hash()

5. create tests for config loading and DB initialization

Do not implement collectors yet.
Keep the schema minimal but aligned with CLAUDE.md.
Run the relevant tests after implementation.


### 2. - Implement the RSS MVP layer.

Tasks:
1. create src/collectors/rss_generic.py
2. parse feed URLs from config
3. normalize feed entries into NormalizedItem
4. support title, url, author, published_at, content_snippet
5. extract preview_image_url from:
   - media_thumbnail
   - media_content
   - enclosure/image-style hints if present
6. add simple keyword filtering using topic_filters from config
7. persist fetched candidates into SQLite
8. create tests for RSS normalization and image extraction using mocked feed data

Do not fetch full article text yet.
Do not implement Claude annotation yet.
Run focused tests only.


### 3. - Implement article-page extraction helpers.

Tasks:
1. create src/extraction.py
2. create src/images.py
3. add helpers to:
   - extract readable text from HTML
   - extract canonical URL
   - extract og:image
   - extract first reasonable article image fallback
4. create tests for og:image and fallback image extraction

Keep the logic simple and deterministic.
Do not add browser automation yet.

### 4. - Implement src/dedupe.py.

Requirements:
- exact URL dedupe
- canonical URL normalization
- title + snippet hash dedupe
- lightweight near-duplicate check using normalized title text

Add tests covering:
- exact duplicates
- same article with UTM params
- similar titles from multiple sources

### 5. Implement rendering.

Tasks:
1. create templates/index.jinja2
2. create src/render.py
3. render a static local HTML file at data/rendered/index.html
4. include sections:
   - Top stories
   - Latest items
   - By source
   - Image highlights
5. each item card must show:
   - title
   - source
   - date
   - tags
   - annotation or fallback snippet
   - original link
   - preview image if available
6. add a tiny client-side search/filter box in vanilla JS
7. create tests validating the output contains key sections and image rendering

Use simple clean HTML/CSS. No framework.

### 6. - Implement Claude annotation.

Tasks:
1. create src/claude/prompts.py to load prompt files from config/prompts
2. create src/claude/summarize.py
3. use Claude Agent SDK or the Anthropic Python client through a clean adapter
4. send a batch of candidate items for annotation
5. enforce strict JSON parsing
6. if Claude call fails, fall back to existing snippets
7. store:
   - keep
   - topic
   - tags
   - annotation
   - why_it_matters
   - priority_score

Keep the interface isolated so it can be swapped later.
Add one unit test for JSON parsing and one failure-path test.

### 7. - Implement the end-to-end MVP pipeline.

Tasks:
1. create src/pipeline.py to orchestrate:
   - config load
   - db init
   - rss collection
   - optional page image enrichment
   - dedupe
   - Claude annotation
   - persistence
   - render
2. create src/scheduler_entry.py as the cron-safe entrypoint
3. create src/main.py with a --smoke-test mode
4. log concise run stats:
   - fetched
   - kept
   - duplicates
   - image_resolved_count
   - rendered_count
5. ensure one collector failure does not abort the whole run

Run the smoke test and focused tests.

### 8. - Implement optional free-integrator support.

Tasks:
1. create src/collectors/rsshub_generic.py
2. add source type handling for optional external-reader references
3. make sure RSSHub-based sources are fully optional
4. do not make pipeline success depend on them
5. add tests for RSSHub generic feed handling

Do not implement Feedly or Inoreader private APIs.
Treat them as operator-side validation references only.

### 9. - Implement X support with production-disabled defaults.

Tasks:
1. create src/collectors/x_api.py
2. support:
   - watched accounts
   - search queries
3. normalize posts into NormalizedItem
4. build canonical post URLs
5. gate execution behind:
   - source enabled flag
   - ENABLE_X_PRODUCTION env flag
6. create src/collectors/x_unofficial.py as an experimental interface only
7. ensure unofficial X logic is never required for successful production runs
8. add tests with mocked responses

Do not scrape X HTML in the main production path.

### 10. - Implement Medium support.

Tasks:
1. create src/collectors/medium_rss.py
2. normalize entries from Medium RSS
3. preserve discovery snippets and any preview image
4. mark items eligible for browser enrichment if configured
5. create src/collectors/medium_browser.py
6. use Playwright persistent context from PLAYWRIGHT_USER_DATA_DIR
7. extract readable text for already-authenticated sessions
8. fail gracefully if login/session is missing
9. add focused tests

Do not hardcode credentials.
Do not attempt to bypass access controls.


