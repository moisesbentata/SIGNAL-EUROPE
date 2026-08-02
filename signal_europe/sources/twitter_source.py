"""Watches X (Twitter) for European tech/VC/AI content via the official
X API v2 search endpoint.

Unlike Instagram, there is no viable free/unofficial route here anymore —
X aggressively blocks scraping and the unofficial libraries that used to
work (snscrape and similar) have been broken since the 2023 API lockdown.
The only reliable option is the official API, which requires a paid tier:
recent-search access starts at the "Basic" plan (~$200/month as of this
writing — verify current pricing at developer.x.com before committing).

This module is a no-op (returns an empty list) until TWITTER_BEARER_TOKEN
is set in .env, so the rest of the pipeline runs fine without it. Only
enable this if the subscription cost is worth it for your use case —
the RSS sources cover most of the same funding/launch news for free.
"""

import os
from pathlib import Path

import yaml

from signal_europe.models import SourceItem

DEFAULT_CONFIG_PATH = Path("config/twitter_watch.yaml")


def _load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def fetch_new_items(config_path: Path = DEFAULT_CONFIG_PATH) -> list:
    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
    if not bearer_token:
        return []  # not configured, skip silently — this source is optional

    import tweepy  # imported lazily so it's only required if this source is used

    config = _load_config(config_path)
    client = tweepy.Client(bearer_token=bearer_token)

    try:
        response = client.search_recent_tweets(
            query=config.get("search_query", "europe startup funding"),
            max_results=min(max(int(config.get("max_results", 15)), 10), 100),
            tweet_fields=["created_at", "author_id", "text"],
        )
    except Exception as exc:  # noqa: BLE001 - best effort, never fatal
        print(f"[twitter_source] search failed: {exc}")
        return []

    items = []
    for tweet in response.data or []:
        text = tweet.text.strip()
        headline = text.split("\n")[0][:150]
        items.append(
            SourceItem(
                source="twitter:search",
                item_id=str(tweet.id),
                headline=headline,
                summary=text[:400],
                url=f"https://x.com/i/web/status/{tweet.id}",
                published=str(getattr(tweet, "created_at", "")),
                tags=["twitter-watch"],
            )
        )
    return items
