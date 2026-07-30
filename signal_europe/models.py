"""Shared data shapes passed between sources, the pipeline, and the
image generator."""

from dataclasses import dataclass, field


@dataclass
class SourceItem:
    source: str          # e.g. "rss:EU-Startups" or "instagram:scaling.europe"
    item_id: str          # stable unique id used for dedup
    headline: str         # short punchy line for the graphic
    summary: str          # 1-3 sentence body text
    url: str = ""          # link back to the original, used in caption
    published: str = ""    # ISO-ish date string, best effort
    tags: list = field(default_factory=list)
