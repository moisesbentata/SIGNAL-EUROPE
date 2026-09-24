"""SQLite-backed post queue.

One table (`queue`) covers both queued-and-pending items and the history
of what's already been posted — status field distinguishes them. This
keeps the schema tiny and lets the weekly repost picker query one place
for "recent posts by reach" without a join.

Columns:
  id                 auto-increment primary key
  source_url         original source URL (or synthetic key for text/media
                     posts) — used for dedup
  video_url          legacy single-video URL, kept for backward compat.
                     New rows leave this empty and use media_items_json.
  media_items_json   JSON array of {"url": ..., "type": "image"|"video"}
                     in slide order. Single-video posts use a 1-element
                     list; carousels use 2-10. When both this and
                     video_url are set, media_items_json wins.
  post_type          "reel" | "photo" | "carousel" — publisher uses this
                     to pick the Graph API flow.
  caption            caption text to publish with
  owner_username     original creator, for the credit line (empty for
                     posts the user authored themselves)
  scheduled_at       ISO UTC timestamp for planned publish time
  is_repost          0/1
  original_queue_id  nullable — for reposts, the queue id of the source
                     post so we can trace lineage / enforce cooldowns
  status             pending | posting | posted | failed | cancelled
  ig_media_id        set on `posted` — Instagram media id from Graph API
  posted_at          ISO UTC timestamp when publish actually completed
  error              nullable, populated on `failed`
  created_at         ISO UTC insert time
"""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _default_db_path() -> Path:
    """Honor DATA_DIR env var so Railway (or any host) can point the DB
    at a persistent volume mount. Falls back to ./data/bot.db for local
    runs."""
    import os

    data_dir = Path(os.getenv("DATA_DIR", "data"))
    return data_dir / "bot.db"


@dataclass
class QueueItem:
    id: int
    source_url: str
    video_url: str
    media_items: list  # list[{"url": str, "type": "image"|"video"}]
    post_type: str  # "reel" | "photo" | "carousel"
    caption: str
    owner_username: str
    scheduled_at: datetime
    is_repost: bool
    original_queue_id: Optional[int]
    status: str
    ig_media_id: Optional[str]
    posted_at: Optional[datetime]
    error: Optional[str]
    created_at: datetime


def _row_col(row: sqlite3.Row, name: str, default=None):
    """Row.__getitem__ raises IndexError for missing cols on some
    SQLite builds; this treats missing as default so we tolerate rows
    created before newer columns were added (in case the persistent DB
    doesn't have them yet)."""
    try:
        val = row[name]
    except (IndexError, KeyError):
        return default
    return val if val is not None else default


def _row_to_item(row: sqlite3.Row) -> QueueItem:
    def _parse(ts: Optional[str]) -> Optional[datetime]:
        if not ts:
            return None
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)

    media_items_json = _row_col(row, "media_items_json")
    if media_items_json:
        try:
            media_items = json.loads(media_items_json)
        except (ValueError, TypeError):
            media_items = []
    else:
        # legacy row with only video_url populated
        legacy_video = row["video_url"] or ""
        media_items = [{"url": legacy_video, "type": "video"}] if legacy_video else []

    post_type = _row_col(row, "post_type") or (
        "carousel" if len(media_items) > 1
        else "photo" if (media_items and media_items[0]["type"] == "image")
        else "reel"
    )

    return QueueItem(
        id=row["id"],
        source_url=row["source_url"],
        video_url=row["video_url"] or "",
        media_items=media_items,
        post_type=post_type,
        caption=row["caption"] or "",
        owner_username=row["owner_username"] or "",
        scheduled_at=_parse(row["scheduled_at"]),
        is_repost=bool(row["is_repost"]),
        original_queue_id=row["original_queue_id"],
        status=row["status"],
        ig_media_id=row["ig_media_id"],
        posted_at=_parse(row["posted_at"]),
        error=row["error"],
        created_at=_parse(row["created_at"]),
    )


class QueueStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_url TEXT NOT NULL,
                    video_url TEXT,
                    media_items_json TEXT,
                    post_type TEXT,
                    caption TEXT,
                    owner_username TEXT,
                    scheduled_at TEXT NOT NULL,
                    is_repost INTEGER NOT NULL DEFAULT 0,
                    original_queue_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    ig_media_id TEXT,
                    posted_at TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            # Additive migrations for existing databases created before
            # these columns existed. SQLite has no IF NOT EXISTS on ADD
            # COLUMN, so we probe pragma_table_info instead.
            existing_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(queue)").fetchall()
            }
            if "media_items_json" not in existing_cols:
                conn.execute("ALTER TABLE queue ADD COLUMN media_items_json TEXT")
            if "post_type" not in existing_cols:
                conn.execute("ALTER TABLE queue ADD COLUMN post_type TEXT")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_source ON queue(source_url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_posted_at ON queue(posted_at)")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -------------------------------------------------------------- dedup

    def find_by_source(self, source_url: str) -> Optional[QueueItem]:
        """Returns the first non-cancelled/non-failed record for this URL,
        so duplicate submissions can be refused with a specific message."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM queue WHERE source_url = ? "
                "AND status IN ('pending', 'posting', 'posted') "
                "ORDER BY id DESC LIMIT 1",
                (source_url,),
            ).fetchone()
        return _row_to_item(row) if row else None

    # -------------------------------------------------------------- write

    def enqueue(
        self,
        source_url: str,
        caption: str,
        owner_username: str,
        scheduled_at: datetime,
        media_items: Optional[list] = None,
        post_type: Optional[str] = None,
        video_url: str = "",
        is_repost: bool = False,
        original_queue_id: Optional[int] = None,
    ) -> int:
        """Enqueue a post.

        Preferred: pass media_items (list of {"url", "type"}) + post_type
        ("reel" | "photo" | "carousel"). Legacy callers may still pass
        video_url instead; it's stored on the row and _row_to_item
        upgrades it back into a 1-element media_items list on read.
        """
        if media_items:
            media_items_json = json.dumps(media_items)
            if not post_type:
                post_type = (
                    "carousel" if len(media_items) > 1
                    else "photo" if media_items[0]["type"] == "image"
                    else "reel"
                )
            # keep video_url populated for the single-video case so any
            # legacy path that reads it still works
            if not video_url and post_type == "reel":
                video_url = media_items[0]["url"]
        else:
            media_items_json = None
            if not post_type:
                post_type = "reel"

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO queue (source_url, video_url, media_items_json,
                                    post_type, caption, owner_username,
                                    scheduled_at, is_repost, original_queue_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_url,
                    video_url,
                    media_items_json,
                    post_type,
                    caption,
                    owner_username,
                    scheduled_at.astimezone(timezone.utc).isoformat(),
                    1 if is_repost else 0,
                    original_queue_id,
                ),
            )
            return cur.lastrowid

    def mark_posting(self, item_id: int) -> bool:
        """Atomic pending->posting transition. Returns False if the item
        was already grabbed (e.g. by a concurrent tick) so the caller
        knows not to double-publish."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE queue SET status = 'posting' WHERE id = ? AND status = 'pending'",
                (item_id,),
            )
            return cur.rowcount == 1

    def mark_posted(self, item_id: int, ig_media_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE queue SET status = 'posted', ig_media_id = ?, "
                "posted_at = ? WHERE id = ?",
                (ig_media_id, datetime.now(timezone.utc).isoformat(), item_id),
            )

    def mark_failed(self, item_id: int, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE queue SET status = 'failed', error = ? WHERE id = ?",
                (error, item_id),
            )

    def cancel(self, item_id: int) -> bool:
        """Only pending items can be cancelled — a posting/posted item
        is either mid-flight or already live and shouldn't be touched."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE queue SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
                (item_id,),
            )
            return cur.rowcount == 1

    def reschedule(self, item_id: int, scheduled_at: datetime) -> None:
        """Used by /next to bump an item to now."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE queue SET scheduled_at = ? WHERE id = ? AND status = 'pending'",
                (scheduled_at.astimezone(timezone.utc).isoformat(), item_id),
            )

    # -------------------------------------------------------------- read

    def pending(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM queue WHERE status = 'pending' ORDER BY scheduled_at ASC"
            ).fetchall()
        return [_row_to_item(r) for r in rows]

    def next_due(self, now: datetime) -> Optional[QueueItem]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM queue WHERE status = 'pending' AND scheduled_at <= ? "
                "ORDER BY scheduled_at ASC LIMIT 1",
                (now.astimezone(timezone.utc).isoformat(),),
            ).fetchone()
        return _row_to_item(row) if row else None

    def last_scheduled(self) -> Optional[datetime]:
        """The furthest-out pending scheduled_at — the answer to "when
        would the next new item land if we chain on to the tail?" """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT scheduled_at FROM queue WHERE status = 'pending' "
                "ORDER BY scheduled_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row["scheduled_at"]).replace(tzinfo=timezone.utc)

    def last_posted_at(self) -> Optional[datetime]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT posted_at FROM queue WHERE status = 'posted' AND posted_at IS NOT NULL "
                "ORDER BY posted_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row["posted_at"]).replace(tzinfo=timezone.utc)

    def posted_since(self, cutoff: datetime) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM queue WHERE status = 'posted' AND posted_at >= ? "
                "ORDER BY posted_at DESC",
                (cutoff.astimezone(timezone.utc).isoformat(),),
            ).fetchall()
        return [_row_to_item(r) for r in rows]

    def already_reposted_recently(self, original_queue_id: int, since: datetime) -> bool:
        """For the weekly picker's cooldown: has this same original post
        been re-queued/reposted more recently than `since`?"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM queue WHERE original_queue_id = ? AND created_at >= ? LIMIT 1",
                (original_queue_id, since.astimezone(timezone.utc).isoformat()),
            ).fetchone()
        return row is not None
