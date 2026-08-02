"""Scores candidate SourceItems so the pipeline can pick the "best" news
when there's more available than the daily posting cadence allows.

Simple, tunable heuristic — not ML. Bump the weights below as you see
what actually performs well once the account has real engagement data.
"""

import re

from signal_europe.models import SourceItem

_MONEY_RE = re.compile(r"[€$£]\s?\d")
_FUNDING_ROUND_RE = re.compile(r"\bseries [a-d]\b|\bseed round\b|\bpre-seed\b", re.IGNORECASE)
_AI_RE = re.compile(r"\bAI\b|artificial intelligence", re.IGNORECASE)
_ACQUISITION_RE = re.compile(r"\bacqui(res|red|sition)\b|\bmerger\b", re.IGNORECASE)


def score_item(item: SourceItem) -> int:
    text = f"{item.headline} {item.summary}"
    score = 0
    if "europe_focused" in item.tags:
        score += 2
    if _MONEY_RE.search(text):
        score += 2
    if _FUNDING_ROUND_RE.search(text):
        score += 2
    if _AI_RE.search(text):
        score += 1
    if _ACQUISITION_RE.search(text):
        score += 1
    if item.company:
        score += 1
    return score


def rank_items(items: list) -> list:
    """Returns items sorted best-first. Stable sort preserves fetch order
    for equal scores, so earlier-fetched (older) items win ties."""
    return sorted(items, key=score_item, reverse=True)
