"""Downloads the video from a public Instagram post/reel URL using
yt-dlp, and extracts enough metadata to credit the original creator in
the repost caption.

This works against Instagram's public web interface, same as any other
unofficial scraping — it's against Instagram's Terms of Service and can
break or get rate-limited without warning. Instagram increasingly
requires a logged-in session even for public reels: set IG_COOKIES_B64
(a base64-encoded cookies.txt exported from a throwaway IG account) and
the downloader will use it. Without it most downloads will fail with
"login required".
"""

import base64
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

DOWNLOAD_DIR = Path("downloads")

# Cached decoded cookies file path; populated on first use and reused.
_COOKIES_TMP_PATH: Path | None = None

_IG_URL_RE = re.compile(
    r"^https?://(www\.)?instagram\.com/(p|reel|reels)/[A-Za-z0-9_-]+/?", re.IGNORECASE
)

# Instagram Graph API's own limit for Reels published via video_url, as of
# this writing — verify current limits at developers.facebook.com before
# relying on this. We check against it after downloading so an oversized
# source file fails loudly instead of silently getting rejected by the API.
GRAPH_API_MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 100 MB


class DownloadError(RuntimeError):
    pass


@dataclass
class DownloadedVideo:
    local_path: Path
    caption: str
    owner_username: str
    source_url: str

    @property
    def size_bytes(self) -> int:
        return self.local_path.stat().st_size

    @property
    def exceeds_graph_api_limit(self) -> bool:
        return self.size_bytes > GRAPH_API_MAX_VIDEO_BYTES


def is_instagram_url(text: str) -> bool:
    return bool(_IG_URL_RE.match(text.strip()))


def _cookies_file_path() -> Path | None:
    """Materialises IG_COOKIES_B64 to a temp file the first time it's
    called, and returns its path on subsequent calls. Returns None when
    the env var isn't set."""
    global _COOKIES_TMP_PATH
    encoded = os.getenv("IG_COOKIES_B64")
    if not encoded:
        return None
    if _COOKIES_TMP_PATH and _COOKIES_TMP_PATH.exists():
        return _COOKIES_TMP_PATH
    try:
        raw = base64.b64decode(encoded)
    except Exception as exc:  # noqa: BLE001
        raise DownloadError(f"IG_COOKIES_B64 is not valid base64: {exc}") from exc
    fd, tmp_path = tempfile.mkstemp(prefix="ig_cookies_", suffix=".txt")
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    _COOKIES_TMP_PATH = Path(tmp_path)
    return _COOKIES_TMP_PATH


def download(url: str) -> DownloadedVideo:
    if not is_instagram_url(url):
        raise DownloadError(f"not a recognized Instagram post/reel URL: {url}")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(DOWNLOAD_DIR / "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        # highest-resolution/highest-bitrate stream available, merged to
        # mp4 if the source has separate video/audio tracks; falls back to
        # a single best progressive stream (the common case for Instagram)
        "format": "bestvideo*+bestaudio/best",
        "format_sort": ["res", "fps", "vbr", "abr"],
        "merge_output_format": "mp4",
        # no re-encoding/postprocessing — whatever yt-dlp pulls down is
        # kept byte-for-byte so nothing degrades quality on the way in
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    cookies_path = _cookies_file_path()
    if cookies_path:
        ydl_opts["cookiefile"] = str(cookies_path)

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
