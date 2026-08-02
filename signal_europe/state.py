"""SQLite-backed dedup store so the pipeline never processes the same
source item twice across runs, plus a daily post counter used to enforce
the posting cadence (see signal_europe/pipeline.py)."""

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path("data/seen_items.db")


class SeenItemStore:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_items (
                    source TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source, item_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_post_counts (
                    post_date TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def is_seen(self, source: str, item_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_items WHERE source = ? AND item_id = ?",
                (source, item_id),
            ).fetchone()
        return row is not None

    def mark_seen(self, source: str, item_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_items (source, item_id) VALUES (?, ?)",
                (source, item_id),
            )

    def posts_today(self, today: date = None) -> int:
        today = today or datetime.now(timezone.utc).date()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count FROM daily_post_counts WHERE post_date = ?",
                (today.isoformat(),),
            ).fetchone()
        return row[0] if row else 0

    def increment_posts_today(self, n: int = 1, today: date = None) -> None:
        today = today or datetime.now(timezone.utc).date()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_post_counts (post_date, count) VALUES (?, ?)
                ON CONFLICT(post_date) DO UPDATE SET count = count + excluded.count
                """,
                (today.isoformat(), n),
            )
