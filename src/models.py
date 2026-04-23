"""Pydantic models for the AI News Agent pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class SourceType(str, Enum):
    rss = "rss"
    arxiv = "arxiv"
    medium_rss = "medium_rss"
    medium_browser = "medium_browser"
    rsshub_generic = "rsshub_generic"
    x_api_accounts = "x_api_accounts"
    x_api_search = "x_api_search"
    x_unofficial = "x_unofficial"
    x_graph_scanner = "x_graph_scanner"
    external_reader_reference = "external_reader_reference"


class SourceCategory(str, Enum):
    primary = "primary"
    research = "research"
    secondary = "secondary"
    optional_integrator = "optional_integrator"
    optional_social = "optional_social"
    experimental = "experimental"


class ItemStatus(str, Enum):
    candidate = "candidate"
    kept = "kept"
    dropped = "dropped"
    duplicate = "duplicate"


class ImageSourceType(str, Enum):
    media_thumbnail = "media_thumbnail"
    media_content = "media_content"
    enclosure = "enclosure"
    og_image = "og_image"
    first_article_image = "first_article_image"
    none = "none"


class SourceConfig(BaseModel):
    """Configuration for a single data source."""

    id: str
    enabled: bool = True
    type: SourceType
    category: SourceCategory = SourceCategory.secondary
    name: str
    feed_urls: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    max_results: Optional[int] = None
    queries: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    enrich_with_browser_if_selected: bool = False


class GlobalConfig(BaseModel):
    """Global pipeline configuration. All values must be supplied via sources.yaml."""

    timezone: str
    output_html: str
    db_path: str
    max_items_per_source: int
    max_fulltext_fetches_per_run: int
    max_claude_batch_items: int
    min_hours_between_refetch: int
    enable_preview_images: bool
    x_enabled_in_production: bool
    claude_model: str
    claude_max_tokens: int
    distill_model: str
    distill_max_tokens: int
    graph_accounts_to_scan: int
    x_top_story_max_ratio: float
    x_api_base_url: str
    x_tweet_base_url: str
    arxiv_api_base_url: str
    user_agent: str
    log_dir: str
    checker_model: str
    checker_max_tokens: int


class TopicFilters(BaseModel):
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)


class ImagePolicy(BaseModel):
    resolution_order: list[str] = Field(default_factory=list)
    hotlink_original_urls: bool = True
    download_locally: bool = False


class RenderConfig(BaseModel):
    sections: list[str]
    item_annotation_word_limit: int
    keep_days: int
    max_top_stories: int
    max_items_in_html: int
    show_preview_images: bool


class AppConfig(BaseModel):
    """Full application configuration loaded from sources.yaml."""

    global_config: GlobalConfig = Field(alias="global")
    topic_filters: TopicFilters = Field(default_factory=TopicFilters)
    image_policy: ImagePolicy = Field(default_factory=ImagePolicy)
    sources: list[SourceConfig] = Field(default_factory=list)
    render: RenderConfig

    model_config = {"populate_by_name": True}


class NormalizedItem(BaseModel):
    """Normalized news item ready for deduplication and annotation."""

    source_id: str
    source_type: str
    title: str
    url: str
    canonical_url: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    content_snippet: Optional[str] = None
    full_text: Optional[str] = None
    preview_image_url: Optional[str] = None
    image_source_type: ImageSourceType = ImageSourceType.none
    tags: list[str] = Field(default_factory=list)
    hash: Optional[str] = None
    status: ItemStatus = ItemStatus.candidate

    @field_validator("title")
    @classmethod
    def title_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title must not be empty")
        return v.strip()


class ClaudeAnnotation(BaseModel):
    """Structured response from Claude annotation pass."""

    keep: bool
    topic: str
    tags: list[str] = Field(default_factory=list)
    annotation: str
    why_it_matters: str
    priority_score: int = Field(ge=0, le=100)



class RunStats(BaseModel):
    """Statistics for a single pipeline run."""

    run_id: Optional[int] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    fetched: int = 0
    kept: int = 0
    duplicates: int = 0
    dropped: int = 0
    image_resolved_count: int = 0
    rendered_count: int = 0
    errors: list[str] = Field(default_factory=list)

    def to_db_dict(self) -> dict[str, Any]:
        """Serialize for SQLite storage."""
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "fetched": self.fetched,
            "kept": self.kept,
            "duplicates": self.duplicates,
            "dropped": self.dropped,
            "image_resolved_count": self.image_resolved_count,
            "rendered_count": self.rendered_count,
        }
