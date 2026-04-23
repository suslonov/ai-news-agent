"""Article page text and metadata extraction helpers."""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def _http_get_page(url: str, timeout: int, user_agent: str) -> str:
    """HTTP GET a page URL with automatic retries on transport errors."""
    resp = httpx.get(url, timeout=timeout, headers={"User-Agent": user_agent}, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def fetch_page(url: str, user_agent: str, timeout: int = _DEFAULT_TIMEOUT) -> Optional[str]:
    """Fetch a URL and return the HTML body, or None on error."""
    try:
        return _http_get_page(url, timeout, user_agent)
    except httpx.HTTPStatusError as exc:
        logger.warning("HTTP error fetching %s: %s", url, exc)
        return None
    except Exception as exc:
        logger.warning("Unexpected error fetching %s: %s", url, exc)
        return None


def extract_readable_text(html: str, max_chars: int = 8000) -> str:
    """Extract clean readable text from an HTML page using readability-lxml.

    Falls back to BeautifulSoup body text if readability fails.
    """
    try:
        from readability import Document

        doc = Document(html)
        readable_html = doc.summary()
        soup = BeautifulSoup(readable_html, "lxml")
        text = soup.get_text(separator=" ", strip=True)
        return text[:max_chars]
    except Exception as exc:
        logger.debug("readability failed, falling back to BS4: %s", exc)

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return text[:max_chars]


def extract_canonical_url(html: str, page_url: str) -> str:
    """Extract the canonical URL from <link rel=canonical> or og:url meta tags.

    Falls back to the original page URL with query-string stripped.
    """
    soup = BeautifulSoup(html, "lxml")

    # <link rel="canonical" href="...">
    tag = soup.find("link", rel="canonical")
    if tag and tag.get("href"):
        href = str(tag["href"]).strip()
        if href.startswith("http"):
            return _normalize_url(href)

    # <meta property="og:url" content="...">
    og_url = soup.find("meta", property="og:url")
    if og_url and og_url.get("content"):
        content = str(og_url["content"]).strip()
        if content.startswith("http"):
            return _normalize_url(content)

    return _normalize_url(page_url)


def _normalize_url(url: str) -> str:
    """Strip tracking query parameters while preserving meaningful ones."""
    tracking_params = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "_ga",
    }
    parsed = urlparse(url)
    if parsed.query:
        qs = parse_qs(parsed.query, keep_blank_values=True)
        cleaned = {k: v for k, v in qs.items() if k.lower() not in tracking_params}
        new_query = urlencode(cleaned, doseq=True)
        parsed = parsed._replace(query=new_query)
    return urlunparse(parsed)


def extract_og_image(html: str, page_url: str = "") -> Optional[str]:
    """Extract the og:image meta tag value from HTML."""
    soup = BeautifulSoup(html, "lxml")

    # <meta property="og:image" content="...">
    tag = soup.find("meta", property="og:image")
    if tag and tag.get("content"):
        url = str(tag["content"]).strip()
        if url:
            return urljoin(page_url, url) if not url.startswith("http") else url

    # <meta name="twitter:image" content="...">
    twitter_tag = soup.find("meta", attrs={"name": "twitter:image"})
    if twitter_tag and twitter_tag.get("content"):
        url = str(twitter_tag["content"]).strip()
        if url:
            return urljoin(page_url, url) if not url.startswith("http") else url

    return None


def extract_first_article_image(html: str, page_url: str = "") -> Optional[str]:
    """Extract the first 'reasonable' image from the article body.

    Skips obvious logos, tracking pixels, and tiny images.
    """
    soup = BeautifulSoup(html, "lxml")

    # Prefer images in <article>, <main>, or <div class="content"> regions
    regions = (
        soup.find("article")
        or soup.find("main")
        or soup.find("div", class_=re.compile(r"content|post|article|body", re.I))
        or soup.body
    )
    if not regions:
        return None

    for img in regions.find_all("img", src=True):
        src = str(img["src"]).strip()
        if not src or src.startswith("data:"):
            continue
        # Resolve relative URLs
        if not src.startswith("http"):
            src = urljoin(page_url, src)
        if not src.startswith("http"):
            continue
        # Skip obvious tracking/logo patterns
        lower = src.lower()
        if any(skip in lower for skip in ("logo", "pixel", "tracking", "analytics", "icon", "avatar")):
            continue
        # Skip tiny images based on explicit width/height attrs
        width = img.get("width")
        height = img.get("height")
        if width and height:
            try:
                if int(width) < 100 or int(height) < 100:
                    continue
            except (ValueError, TypeError):
                pass
        return src

    return None
