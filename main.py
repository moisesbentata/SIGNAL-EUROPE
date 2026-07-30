"""Entry point: run one pass of the content pipeline.

Usage:
    python main.py

Intended to be run on a schedule (cron, launchd, systemd timer, GitHub
Actions schedule, etc) — each run fetches new items, dedupes against
data/seen_items.db, and either queues drafts in output/review_queue/ or
auto-publishes, depending on AUTO_PUBLISH in .env.
"""

from signal_europe.pipeline import run

if __name__ == "__main__":
    run()
