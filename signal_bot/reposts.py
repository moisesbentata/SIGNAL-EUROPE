"""Weekly repost picker — runs once a week, finds the best-performing
recent posts, and adds them back to the queue spread across the coming
week.

Selection criteria, in order:
  1. status = 'posted' AND posted_at within the last WEEKLY_REPOST_LOOKBACK_DAYS
  2. posted_at at least WEEKLY_REPOST_MIN_AGE_HOURS old (newer than
     that hasn't had time to accumulate representative reach numbers)
  3. NOT already reposted in the last WEEKLY_REPOST_COOLDOWN_DAYS
     (avoid cycling the same viral video every week)
  4. NOT itself a repost (we score originals only — reposts of reposts
     of reposts get boring fast)
  5. sorted by fresh reach fetched from the Graph API (highest first)
  6. top WEEKLY_REPOST_COUNT picked

Each pick is scheduled at a slot spread across the coming week (see
scheduler.spread_reposts_over_week) with a small caption prefix so
Instagram doesn't flag it as identical duplicate content.
"""

import os
from datetime import datetime, timedelta, timezone

from signal_bot import graph_api, scheduler
from signal_bot.queue import QueueStore


def _config():
    return {
        "enabled": os.getenv("WEEKLY_REPOST_ENABLED", "true").strip().lower() == "true",
        "count": int(os.getenv("WEEKLY_REPOST_COUNT", "3")),
        "lookback_days": int(os.getenv("WEEKLY_REPOST_LOOKBACK_DAYS", "7")),
        "min_age_hours": int(os.getenv("WEEKLY_REPOST_MIN_AGE_HOURS", "48")),
        "cooldown_days": int(os.getenv("WEEKLY_REPOST_COOLDOWN_DAYS", "30")),
        "prefix": os.getenv("WEEKLY_REPOST_PREFIX", "Back by demand —"),
    }


def _prefixed_caption(prefix: str, original: str) -> str:
    prefix = prefix.strip()
    if not prefix:
        return original
    # Avoid double-prefixing if a post was already prefixed (e.g. the
    # same top performer wins two weeks in a row)
    if original.lstrip().startswith(prefix):
        return original
    return f"{prefix} {original}"


def run_weekly_repost(store: QueueStore) -> list:
    """Executes one weekly-repost run. Returns the list of enqueued
    QueueItem ids (empty if disabled or nothing qualified)."""
    cfg = _config()
    if not cfg["enabled"]:
        return []

    now = datetime.now(timezone.utc)
    lookback_cutoff = now - timedelta(days=cfg["lookback_days"])
    max_posted_at = now - timedelta(hours=cfg["min_age_hours"])
    cooldown_cutoff = now - timedelta(days=cfg["cooldown_days"])

    candidates = [
        item
        for item in store.posted_since(lookback_cutoff)
        if item.posted_at is not None
        and item.posted_at <= max_posted_at
        and not item.is_repost
        and item.ig_media_id
        and not store.already_reposted_recently(item.id, cooldown_cutoff)
    ]

    if not candidates:
        return []

    # Fetch fresh reach per candidate — this is what the picker sorts on.
    # A candidate that returns 0 (insight not yet available, or fetch
    # failed) is deprioritized but not excluded, so a first-run with no
    # insight history still produces *some* reposts.
    scored = [(item, graph_api.get_media_reach(item.ig_media_id)) for item in candidates]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    picks = [item for item, _ in scored[: cfg["count"]]]

    slots = scheduler.spread_reposts_over_week(len(picks), now)

    enqueued_ids = []
    for item, slot in zip(picks, slots):
        new_caption = _prefixed_caption(cfg["prefix"], item.caption)
        new_id = store.enqueue(
            source_url=item.source_url,
            video_url=item.video_url,
            caption=new_caption,
            owner_username=item.owner_username,
            scheduled_at=slot,
            is_repost=True,
            original_queue_id=item.id,
        )
        enqueued_ids.append(new_id)

    return enqueued_ids
