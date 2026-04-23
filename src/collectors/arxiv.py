"""arXiv Atom API collector.

Queries the arXiv public API (https://export.arxiv.org/api/) for recent
papers in configured categories and returns NormalizedItems.

The API base URL is injected from GlobalConfig (sources.yaml).
Each query runs independently; duplicates across queries are deduplicated
by canonical URL before returning.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional
from xml.etree import ElementTree

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.models import ItemStatus, NormalizedItem, SourceConfig, TopicFilters

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 20
_ATOM_NS = "http://www.w3.org/2005/Atom"
_NS = {"atom": _ATOM_NS}


def _compute_hash(arxiv_id: str) -> str:
    return hashlib.sha256(f"arxiv_{arxiv_id}".encode()).hexdigest()[:16]


def _extract_arxiv_id(id_url: str) -> str:
    """Extract the short arXiv ID from the full abs URL.

    "https://arxiv.org/abs/2301.12345v2" → "2301.12345v2"
    """
    return id_url.rstrip("/").rsplit("/", 1)[-1]


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _http_fetch(url: str, params: dict, user_agent: str) -> str:
    resp = httpx.get(
        url,
        params=params,
        headers={"User-Agent": user_agent},
        timeout=_DEFAULT_TIMEOUT,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.text


def _fetch_query(query: str, api_base: str, max_results: int, user_agent: str) -> str:
    """Fetch the Atom feed for one arXiv search query."""
    return _http_fetch(
        f"{api_base}/query",
        params={
            "search_query": query,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
        user_agent=user_agent,
    )


def _parse_feed(xml_text: str, source: SourceConfig) -> list[NormalizedItem]:
    """Parse an arXiv Atom response into NormalizedItems."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        logger.warning("arXiv XML parse error: %s", exc)
        return []

    items: list[NormalizedItem] = []
    for entry in root.findall("atom:entry", _NS):
        try:
            item = _parse_entry(entry, source)
            if item:
                items.append(item)
        except Exception as exc:
            logger.warning("arXiv: failed to parse entry: %s", exc)
    return items


def _parse_entry(entry: ElementTree.Element, source: SourceConfig) -> Optional[NormalizedItem]:
    """Convert one Atom entry element into a NormalizedItem."""
    raw_id = (entry.findtext("atom:id", default="", namespaces=_NS) or "").strip()
    title_el = entry.find("atom:title", _NS)
    title = " ".join((title_el.text or "").split()) if title_el is not None else ""

    if not raw_id or not title:
        return None

    arxiv_id = _extract_arxiv_id(raw_id)
    url = f"https://arxiv.org/abs/{arxiv_id}"

    summary_el = entry.find("atom:summary", _NS)
    abstract = " ".join((summary_el.text or "").split()) if summary_el is not None else ""

    # Up to 3 authors, then "et al."
    author_names = []
    for author_el in entry.findall("atom:author", _NS):
        name_el = author_el.find("atom:name", _NS)
        if name_el is not None and name_el.text:
            author_names.append(" ".join(name_el.text.split()))
    author_str: Optional[str] = None
    if author_names:
        author_str = ", ".join(author_names[:3])
        if len(author_names) > 3:
            author_str += " et al."

    published_at: Optional[datetime] = None
    pub_text = (entry.findtext("atom:published", default="", namespaces=_NS) or "").strip()
    if pub_text:
        try:
            published_at = datetime.fromisoformat(pub_text.replace("Z", "+00:00"))
        except Exception:
            pass

    # Collect arXiv category tags
    category_terms = [
        el.get("term", "")
        for el in entry.findall("atom:category", _NS)
        if el.get("term")
    ]
    tags = list(source.tags) + [t for t in category_terms if t not in source.tags]

    return NormalizedItem(
        source_id=source.id,
        source_type=source.type.value,
        title=title,
        url=url,
        canonical_url=url,
        author=author_str,
        published_at=published_at,
        content_snippet=abstract[:500] or None,
        hash=_compute_hash(arxiv_id),
        tags=tags,
        status=ItemStatus.candidate,
    )


def _passes_filters(item: NormalizedItem, filters: TopicFilters) -> bool:
    """Return True when the item matches topic filters (or filters are empty)."""
    if not filters.include_keywords:
        return True
    text = f"{item.title} {item.content_snippet or ''}".lower()
    return any(kw.lower() in text for kw in filters.include_keywords)


def collect(
    source: SourceConfig,
    filters: TopicFilters,
    api_base: str,
    user_agent: str,
    max_items: int = 20,
) -> list[NormalizedItem]:
    """Collect recent arXiv papers for all queries defined in *source*.

    Each query is fetched independently with `max_results` capped at
    source.max_results (per query) or a proportional share of max_items.
    Duplicates across queries are dropped by canonical URL.
    """
    if not source.queries:
        logger.info("arXiv source %s has no queries configured, skipping.", source.id)
        return []

    max_per_query = source.max_results or max(1, max_items // len(source.queries))
    results: list[NormalizedItem] = []
    seen_urls: set[str] = set()

    for query in source.queries:
        logger.info("Fetching arXiv query: %s (max_results=%d)", query, max_per_query)
        try:
            xml_text = _fetch_query(query, api_base, max_per_query, user_agent)
        except Exception as exc:
            logger.warning("arXiv fetch failed for query '%s': %s", query, exc)
            continue

        # Collect all entries from this query; do NOT break early so every
        # configured query is always attempted (avoids silently dropping queries).
        for item in _parse_feed(xml_text, source):
            if item.url in seen_urls:
                continue
            seen_urls.add(item.url)
            if _passes_filters(item, filters):
                results.append(item)

    # Trim to max_items only after all queries have been fetched.
    results = results[:max_items]
    logger.info("arXiv source %s: collected %d papers", source.id, len(results))
    return results
