"""Scheduling logic — decides *when* a queued item is allowed to publish.

Two layers, both applied:

  1. **At enqueue time** (`next_slot_after`): pick a scheduled_at for a
     new item. Chains onto whatever's already the last-scheduled thing
     in the queue plus a random gap in [MIN_POST_GAP_HOURS,
     MAX_POST_GAP_HOURS], snapped to the next non-quiet-hour if it
     lands in the quiet window.

  2. **At execution time** (`can_publish_now`): even if a scheduled_at
     has arrived, the tick loop double-checks two constraints:
       - we're not currently inside quiet hours
       - it's been at least MIN_POST_GAP_HOURS since the *actual* last
         posted_at (guards against reposts inserted by the weekly job
         landing too close to a fresh submission)

All comparisons happen in the configured local timezone
(POSTING_TIMEZONE, defaults to Europe/Madrid) so "quiet hours"
means what a human means. Storage stays UTC.
"""

import os
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


def _tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("POSTING_TIMEZONE", "Europe/Madrid"))


def _quiet_window() -> tuple:
    """Returns (start_hour, end_hour) — start > end means the window
    wraps midnight (e.g. 23..8 = 11pm through 8am)."""
    start = int(os.getenv("QUIET_HOURS_START", "23"))
    end = int(os.getenv("QUIET_HOURS_END", "8"))
    return start, end


def _gap_range() -> tuple:
    lo = float(os.getenv("MIN_POST_GAP_HOURS", "4"))
    hi = float(os.getenv("MAX_POST_GAP_HOURS", "5"))
    return lo, hi


def is_quiet_hour(dt: datetime) -> bool:
    """dt is expected to be tz-aware in any zone — converted to POSTING_TIMEZONE."""
    local = dt.astimezone(_tz())
    start, end = _quiet_window()
    if start == end:
        return False  # no quiet window configured
    hour = local.hour
    if start < end:
        return start <= hour < end
    # wraps midnight: e.g. 23..8 means hours >= 23 OR hours < 8
    return hour >= start or hour < end


def _shift_out_of_quiet(dt: datetime) -> datetime:
    """If dt lands in quiet hours, push it to the moment the quiet
    window ends. Returns dt unchanged otherwise."""
    if not is_quiet_hour(dt):
        return dt
    local = dt.astimezone(_tz())
    _, end_hour = _quiet_window()
    target = local.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if target <= local:
        target = target + timedelta(days=1)
    return target.astimezone(timezone.utc)


def next_slot_after(reference: datetime) -> datetime:
    """Given a reference point (typically the last-scheduled item's
    scheduled_at, or "now" for an empty queue), return the next valid
    scheduled_at — reference + random gap, shifted past quiet hours if
    needed."""
    lo, hi = _gap_range()
    gap = random.uniform(lo, hi)
    candidate = reference + timedelta(hours=gap)
    return _shift_out_of_quiet(candidate)


def compute_enqueue_slot(last_scheduled: Optional[datetime], now: datetime) -> datetime:
    """Public entry point used by the bot when adding a new item."""
    reference = max(last_scheduled, now) if last_scheduled else now
    return next_slot_after(reference)


def can_publish_now(now: datetime, last_posted_at: Optional[datetime]) -> tuple:
    """Execution-time gate. Returns (allowed: bool, reason: str). The
    reason string is empty on allowed=True; on False it's a short
    log-friendly explanation of why we're holding off."""
    if is_quiet_hour(now):
        return False, "inside quiet hours"
    if last_posted_at is not None:
        min_gap_hours, _ = _gap_range()
        elapsed = now - last_posted_at
        if elapsed < timedelta(hours=min_gap_hours):
            mins_left = int((timedelta(hours=min_gap_hours) - elapsed).total_seconds() / 60)
            return False, f"only {elapsed} since last post, waiting {mins_left}m more"
    return True, ""


def spread_reposts_over_week(count: int, now: datetime) -> list:
    """For the Sunday weekly job — returns `count` scheduled_at
    timestamps evenly spread across the next 7 days, each snapped out
    of quiet hours and jittered by ±30min so they don't land at exactly
    identical clock times week over week."""
    if count <= 0:
        return []
    day_span = 7
    slot_days = day_span / (count + 1)  # e.g. count=3 -> ~1.75 day spacing
    times = []
    for i in range(count):
        offset_days = slot_days * (i + 1)
        target = now + timedelta(days=offset_days)
        # jitter within ±30min so weekly reruns don't stack at the same clock time
        target += timedelta(minutes=random.uniform(-30, 30))
        times.append(_shift_out_of_quiet(target))
    return times
