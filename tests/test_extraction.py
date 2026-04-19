"""Tests for HTML extraction helpers and image resolution."""

from __future__ import annotations

import pytest

from src.extraction import (
    _normalize_url,
    extract_canonical_url,
    extract_first_article_image,
    extract_og_image,
    extract_readable_text,
)


SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Test Page</title>
  <meta property="og:image" content="https://example.com/og-image.jpg">
  <meta property="og:url" content="https://example.com/article-canonical">
  <link rel="canonical" href="https://example.com/canonical-link">
</head>
<body>
  <article>
    <h1>Main Article Heading</h1>
    <p>This is the main article content with substantial text about AI.</p>
    <img src="https://example.com/article-image.jpg" width="800" height="600" alt="main">
    <p>More content here.</p>
  </article>
  <nav>Navigation links</nav>
  <footer>Footer content</footer>
</body>
</html>"""


def test_extract_og_image():
    url = extract_og_image(SAMPLE_HTML, "https://example.com/article")
    assert url == "https://example.com/og-image.jpg"


def test_extract_og_image_missing():
    html = "<html><head></head><body>No image here.</body></html>"
    assert extract_og_image(html) is None


def test_extract_og_image_twitter_fallback():
    html = """<html><head>
    <meta name="twitter:image" content="https://example.com/twitter.jpg">
    </head><body></body></html>"""
    url = extract_og_image(html)
    assert url == "https://example.com/twitter.jpg"


def test_extract_canonical_url_link_tag():
    url = extract_canonical_url(SAMPLE_HTML, "https://example.com/article?utm_source=x")
    assert url == "https://example.com/canonical-link"


def test_extract_canonical_url_og_url_fallback():
    html = """<html><head>
    <meta property="og:url" content="https://example.com/og-canonical">
    </head><body></body></html>"""
    url = extract_canonical_url(html, "https://example.com/article")
    assert url == "https://example.com/og-canonical"


def test_extract_canonical_url_fallback_strips_utm():
    html = "<html><head></head><body></body></html>"
    url = extract_canonical_url(html, "https://example.com/article?utm_source=rss&id=1")
    # utm_source stripped, id preserved
    assert "utm_source" not in url
    assert "id=1" in url


def test_normalize_url_strips_utm():
    url = _normalize_url("https://example.com/page?utm_source=feed&utm_medium=rss&id=42")
    assert "utm_source" not in url
    assert "id=42" in url


def test_extract_first_article_image():
    url = extract_first_article_image(SAMPLE_HTML, "https://example.com")
    assert url == "https://example.com/article-image.jpg"


def test_extract_first_article_image_skips_tiny():
    html = """<html><body><article>
    <img src="https://example.com/pixel.png" width="1" height="1">
    <img src="https://example.com/real.jpg" width="640" height="480">
    </article></body></html>"""
    url = extract_first_article_image(html, "https://example.com")
    assert url == "https://example.com/real.jpg"


def test_extract_first_article_image_skips_logo():
    html = """<html><body><article>
    <img src="https://example.com/logo.png">
    <img src="https://example.com/photo.jpg">
    </article></body></html>"""
    url = extract_first_article_image(html, "https://example.com")
    assert url == "https://example.com/photo.jpg"


def test_extract_first_article_image_no_images():
    html = "<html><body><article><p>Text only</p></article></body></html>"
    assert extract_first_article_image(html) is None


def test_extract_readable_text():
    text = extract_readable_text(SAMPLE_HTML)
    assert "Main Article Heading" in text
    assert "main article content" in text
    # Navigation/footer text may or may not be included depending on readability
