"""Keyword-heuristic filter that keeps the pipeline on-brand: European
companies/tech/AI/VC news only. Applied to items from broad, non-European
feeds (TechCrunch, The Verge, Business Insider) — items already tagged
"europe_focused" (from a feed scoped to Europe, e.g. EU-Startups, Sifted)
skip this check entirely and are trusted as-is.

This is a heuristic, not a guarantee — it will occasionally misfire in
both directions. Tune the keyword lists below as you see false
positives/negatives in the review queue.
"""

import re

from signal_europe.models import SourceItem

EUROPEAN_COUNTRIES = [
    "europe", "european", "eu ", "european union",
    "germany", "german", "france", "french", "uk", "united kingdom",
    "britain", "british", "england", "scotland", "wales", "ireland", "irish",
    "spain", "spanish", "italy", "italian", "netherlands", "dutch",
    "belgium", "belgian", "sweden", "swedish", "norway", "norwegian",
    "denmark", "danish", "finland", "finnish", "switzerland", "swiss",
    "austria", "austrian", "poland", "polish", "portugal", "portuguese",
    "greece", "greek", "czech", "hungary", "hungarian", "romania", "romanian",
    "bulgaria", "bulgarian", "croatia", "croatian", "slovakia", "slovenian",
    "estonia", "estonian", "latvia", "latvian", "lithuania", "lithuanian",
    "iceland", "icelandic", "luxembourg", "ukraine", "ukrainian",
]

EUROPEAN_CITIES = [
    "berlin", "munich", "hamburg", "frankfurt", "paris", "london",
    "amsterdam", "rotterdam", "madrid", "barcelona", "milan", "rome",
    "stockholm", "oslo", "copenhagen", "helsinki", "zurich", "geneva",
    "vienna", "warsaw", "krakow", "lisbon", "athens", "prague", "budapest",
    "dublin", "brussels", "tallinn", "vilnius", "riga", "bucharest",
]

TECH_KEYWORDS = [
    "startup", "start-up", "funding", "fundraise", "fundraising", "raises",
    "raised", "series a", "series b", "series c", "series d", "seed round",
    "venture capital", "vc firm", "valuation", "ipo", "acquisition",
    "acquires", "acquired", "merger", "artificial intelligence", " ai ",
    "ai startup", "machine learning", "llm", "software", "saas", "app",
    "tech company", "technology", "unicorn", "investor", "investment round",
    "pre-seed", "venture-backed",
]

_EUROPE_RE = re.compile(
    "|".join(re.escape(w) for w in EUROPEAN_COUNTRIES + EUROPEAN_CITIES),
    re.IGNORECASE,
)
_TECH_RE = re.compile("|".join(re.escape(w) for w in TECH_KEYWORDS), re.IGNORECASE)


def _text(item: SourceItem) -> str:
    return f" {item.headline} {item.summary} "


def is_european(item: SourceItem) -> bool:
    return bool(_EUROPE_RE.search(_text(item)))


def is_tech_relevant(item: SourceItem) -> bool:
    return bool(_TECH_RE.search(_text(item)))


def is_on_brand(item: SourceItem) -> bool:
    """European company/tech/AI/VC news only. Items from feeds already
    scoped to Europe (tagged europe_focused) are trusted without the
    keyword check — they're inherently on-topic by source."""
    if "europe_focused" in item.tags:
        return True
    return is_european(item) and is_tech_relevant(item)


def filter_items(items: list) -> list:
    return [i for i in items if is_on_brand(i)]
