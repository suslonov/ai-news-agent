
# AI Twitter Graph Builder (Budget-Constrained)

- it is an additional system for the existing news scanning, I need a mix of news and twits, ranged by Claude as existing

- I need both graph builder and scanner

- the maximum share of twits should be ~20% of all top news

- build it, but respect ENABLE_X_PRODUCTION=false in .env

## Objective
Build and maintain a high-signal Twitter/X graph for AI news and insights, minimizing API cost and noise.

---

## Core Principle

The system is NOT a list collector.

It is a graph expansion + pruning system:

seed → expand → score → prune → stabilize

---

## Data Model

keep data at the same db as other sources

Account:
- handle
- category (research / builder / news / indie)
- score
- last_seen
- source (seed / discovered / recommended)

Edge:
- type (follow / mention / retweet / reply)
- weight

- move twitter accounts list to a separate sources file

---

## Phase 1 — Seed Initialization (manual, one-time)

Input: 30–50 curated accounts

Cost: $0

---

## Phase 2 — Graph Expansion

### Sources (ordered by cost-efficiency)

1. Following lists (SCRAPE)
   - cost: near-zero
   - extract top N accounts from seed.following

2. Mentions / replies (SCRAPE)
   - detect recurring handles

3. Retweets
   - high signal amplification

4. Lists (optional)
   - scrape public Twitter Lists

---

## Expansion Algorithm

For each seed_account:
    get following (limit 100–200)
    get recent tweets (limit 50)

    extract:
        mentioned_accounts
        retweeted_accounts

    add to candidate pool

---

## Phase 3 — Scoring

Score(account) =

    + frequency_of_appearance
    + mutual_connections
    + engagement_ratio
    + follower_quality (optional)

    - spam_penalty
    - marketing_bias

---

## Heuristics

High-quality accounts:
- referenced by multiple seeds
- low posting frequency but high engagement
- technical content

Low-quality accounts:
- high frequency + low depth
- "AI hype", clickbait
- repeated recycled content

---

## Phase 4 — Pruning

Keep:
- top 100–150 accounts

Remove:
- bottom 50% by score
- accounts not seen in 30 days

---

## Phase 5 — Stabilization

Weekly loop:

1. re-score graph
2. remove dead nodes
3. expand from top 20 nodes only

---

## Budget Constraints

- do use X API

### Avoid:
- full firehose ingestion

### Use instead:
- cached HTML snapshots
- incremental updates

---

## Optimization

- Cache following lists (TTL: 7 days)
- Cache tweets (TTL: 1–3 days)
- Deduplicate accounts aggressively

---

## Output

System produces:

1. curated account list
2. clusters (research / builders / etc.)
3. HTML report:
   - top accounts
   - trending nodes
   - suggested follows

---

## Optional: LLM Layer

Use Claude Sonnet for:
- classification
- spam detection
- summarization

Avoid:
- sending raw timelines (too expensive)

---

## Failure Modes

- echo chamber (same cluster)
- overfitting to hype accounts
- stale graph

Mitigation:
- inject new random seeds weekly
- enforce diversity by category

---

## Minimal Viable Version

Day 1:
- seeds + manual expansion

Day 2:
- scraping following

Day 3:
- scoring + pruning

Day 4:
- HTML output

---

## Key Insight

The value is NOT in who you follow.

It is in how fast your graph adapts.





