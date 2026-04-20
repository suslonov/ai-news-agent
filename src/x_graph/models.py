"""Pydantic models for the Twitter/X graph builder."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TwitterAccount(BaseModel):
    """A node in the Twitter account graph."""

    handle: str
    category: str = "news"
    score: float = 0.0
    last_seen: Optional[str] = None
    source: str = "seed"
    active: bool = True
    appearance_count: int = 0


class TwitterEdge(BaseModel):
    """A directed edge between two accounts in the graph."""

    from_handle: str
    to_handle: str
    edge_type: str
    weight: float = 1.0
    seen_count: int = 1


class TwitterSeed(BaseModel):
    """A single seed account from twitter_seeds.yaml."""

    handle: str
    category: str = "news"


class TwitterSeedsConfig(BaseModel):
    """Schema for config/twitter_seeds.yaml."""

    seeds: list[TwitterSeed]
