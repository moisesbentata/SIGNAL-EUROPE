"""Best-effort watcher for a small list of public Instagram accounts.

IMPORTANT: Instagram has no official API for reading posts on accounts
you don't own. This uses `instaloader` against the public web interface,
which is against Instagram's Terms of Service and can break or trigger
rate-limiting/blocks at any time without warning. Treat this module as
optional and disposable, not the reliable part of the pipeline (that's
rss_source.py). Run it with a throwaway login, never your real posting
account's credentials.

Only caption text and post metadata are extracted — never the source
account's images/video, which stay uncopied.
"""

import os
from pathlib import Path

import instaloader
import yaml

from signal_europe.models import SourceItem

DEFAULT_ACCOUNTS_PATH = Path("config/watched_accounts.yaml")


def load_account_list(accounts_path: Path = DEFAULT_ACCOUNTS_PATH) -> list:
    with open(accounts_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("accounts", [])


def _build_loader() -> instaloader.Instaloader:
    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
    )
    username = os.getenv("IG_WATCH_USERNAME")
    password = os.getenv("IG_WATCH_PASSWORD")
    if username and password:
        try:
            loader.login(username, password)
        except Exception as exc:  # noqa: BLE001 - best effort, never fatal
            print(f"[instagram_source] login failed, continuing anonymously: {exc}")
    return loader


def fetch_new_items(
    accounts_path: Path = DEFAULT_ACCOUNTS_PATH, max_posts_per_account: int = 5
) -> list:
    """Returns a list of SourceItem for recent posts on each watched
    account. Any failure (rate limit, blocked, private account, network
    error) is caught per-account so one bad account doesn't kill the run.
    """
    items = []
    accounts = load_account_list(accounts_path)
    if not accounts:
        return items

    loader = _build_loader()

    for username in accounts:
        try:
            profile = instaloader.Profile.from_username(loader.context, username)
            for i, post in enumerate(profile.get_posts()):
                if i >= max_posts_per_account:
                    break
                caption = (post.caption or "").strip()
                headline = caption.split("\n")[0][:120] if caption else f"New post from @{username}"
                items.append(
                    SourceItem(
                        source=f"instagram:{username}",
                        item_id=post.shortcode,
                        headline=headline,
                        summary=caption[:400],
                        url=f"https://www.instagram.com/p/{post.shortcode}/",
                        published=str(post.date_utc),
                        tags=["instagram-watch"],
                    )
                )
        except Exception as exc:  # noqa: BLE001 - best effort per account
            print(f"[instagram_source] failed to fetch @{username}: {exc}")
            continue

    return items
