"""Downloads the video from a public Instagram post/reel URL using
yt-dlp, and extracts enough metadata to credit the original creator in
the repost caption.

This works against Instagram's public web interface, same as any other
unofficial scraping — it's against Instagram's Terms of Service and can
break or get rate-limited without warning. It only works for posts that
are actually public; private accounts/posts will fail.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

DOWNLOAD_DIR = Path("downloads")

_IG_URL_RE = re.compile(
    r"^https?://(www\.)?instagram\.com/(p|reel|reels)/[A-Za-z0-9_-]+/?", re.IGNORECASE
)


class DownloadError(RuntimeError):
    pass


@dataclass
class DownloadedVideo:
    local_path: Path
    caption: str
    owner_username: str
    source_url: str


def is_instagram_url(text: str) -> bool:
    return bool(_IG_URL_RE.match(text.strip()))


def download(url: str) -> DownloadedVideo:
    if not is_instagram_url(url):
        raise DownloadError(f"not a recognized Instagram post/reel URL: {url}")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(DOWNLOAD_DIR / "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "mp4/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001
        raise DownloadError(f"failed to download {url}: {exc}") from exc

    local_path = Path(ydl.prepare_filename(info))
    if not local_path.exists():
        # yt-dlp sometimes remuxes to a different extension than requested
        candidates = list(DOWNLOAD_DIR.glob(f"{info.get('id', '')}.*"))
        if not candidates:
            raise DownloadError(f"download reported success but no file found for {url}")
        local_path = candidates[0]

    return DownloadedVideo(
        local_path=local_path,
        caption=(info.get("description") or "").strip(),
        owner_username=info.get("uploader_id") or info.get("uploader") or "",
        source_url=url,
    )
