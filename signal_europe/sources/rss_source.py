"""Pulls new items from the RSS feeds listed in config/feeds.yaml.

This is the reliable backbone of the pipeline: official feeds, no ToS
risk, no scraping. Each feed entry is turned into a SourceItem using the
feed's own title/summary text (never the source's images).
"""

import html
import re
from pathlib import Path

import feedparser
import yaml

from signal_europe.models import SourceItem

DEFAULT_FEEDS_PATH = Path("config/feeds.yaml")


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()


def load_feed_list(feeds_path: Path = DEFAULT_FEEDS_PATH) -> list:
    with open(feeds_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("feeds", [])


def fetch_new_items(feeds_path: Path = DEFAULT_FEEDS_PATH) -> list:
    """Returns a list of SourceItem for every entry in every configured
    feed. Caller is responsible for filtering out already-seen items via
    the dedup store."""
    items = []
    for feed in load_feed_list(feeds_path):
        name = feed["name"]
        europe_focused = bool(feed.get("europe_focused", False))
        parsed = feedparser.parse(feed["url"])
        for entry in parsed.entries:
            item_id = getattr(entry, "id", None) or getattr(entry, "link", None)
            if not item_id:
                continue
            headline = _strip_html(getattr(entry, "title", ""))
            summary = _strip_html(getattr(entry, "summary", ""))
            # keep the summary tight, this is a graphic caption not an article
            if len(summary) > 400:
                summary = summary[:397].rsplit(" ", 1)[0] + "..."
            tags = ["news"]
            if europe_focused:
                tags.append("europe_focused")
            items.append(
                SourceItem(
                    source=f"rss:{name}",
                    item_id=item_id,
                    headline=headline,
                    summary=summary,
                    url=getattr(entry, "link", ""),
                    published=getattr(entry, "published", ""),
                    tags=tags,
                )
            )
    return items
