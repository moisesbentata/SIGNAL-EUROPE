"""Downloads the video from a public Instagram or Twitter/X URL using
yt-dlp, and extracts enough metadata to credit the original creator in
the repost caption.

Both platforms serve public posts through their normal web frontend and
both are covered by yt-dlp out of the box. Both are also increasingly
gated: Instagram usually requires a session cookie for anything but the
most public reels; Twitter's guest read has been shrinking too. Set
IG_COOKIES_B64 / TWITTER_COOKIES_B64 (base64-encoded cookies.txt
exported from a throwaway account) to pass credentials to yt-dlp when
anonymous fetches fail. Both are optional — the downloader falls back
to no cookies when the env var is absent.

Using logged-in cookies to fetch other people's public posts is against
both platforms' terms of service, so use a throwaway account for these,
never your real posting account.
"""

import base64
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

DOWNLOAD_DIR = Path("downloads")

_IG_URL_RE = re.compile(
    r"^https?://(www\.)?instagram\.com/(p|reel|reels)/[A-Za-z0-9_-]+/?", re.IGNORECASE
)
_TWITTER_URL_RE = re.compile(
    r"^https?://(www\.|mobile\.)?(twitter\.com|x\.com)/[A-Za-z0-9_]+/status/\d+/?",
    re.IGNORECASE,
)

# per-platform cookie env var + cached materialised path
_COOKIE_ENV = {
    "instagram": "IG_COOKIES_B64",
    "twitter": "TWITTER_COOKIES_B64",
}
_COOKIES_TMP_PATHS: dict = {}

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
    platform: str  # "instagram" or "twitter"

    @property
    def size_bytes(self) -> int:
        return self.local_path.stat().st_size

    @property
    def exceeds_graph_api_limit(self) -> bool:
        return self.size_bytes > GRAPH_API_MAX_VIDEO_BYTES


def is_instagram_url(text: str) -> bool:
    return bool(_IG_URL_RE.match(text.strip()))


def is_twitter_url(text: str) -> bool:
    return bool(_TWITTER_URL_RE.match(text.strip()))


def is_supported_url(text: str) -> bool:
    return is_instagram_url(text) or is_twitter_url(text)


def detect_platform(url: str) -> str:
    if is_instagram_url(url):
        return "instagram"
    if is_twitter_url(url):
        return "twitter"
    raise DownloadError(f"unrecognised URL (need Instagram or Twitter/X): {url}")


def _cookies_file_path(platform: str) -> Path | None:
    """Materialises the platform's cookies env var to a temp file the
    first time it's called, and returns its path on subsequent calls.
    Returns None when the env var isn't set."""
    env_name = _COOKIE_ENV.get(platform)
    if not env_name:
        return None
    encoded = os.getenv(env_name)
    if not encoded:
        return None
    cached = _COOKIES_TMP_PATHS.get(platform)
    if cached and cached.exists():
        return cached
    try:
        raw = base64.b64decode(encoded)
    except Exception as exc:  # noqa: BLE001
        raise DownloadError(f"{env_name} is not valid base64: {exc}") from exc
    fd, tmp_path = tempfile.mkstemp(prefix=f"{platform}_cookies_", suffix=".txt")
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    path = Path(tmp_path)
    _COOKIES_TMP_PATHS[platform] = path
    return path


def download(url: str) -> DownloadedVideo:
    platform = detect_platform(url)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(DOWNLOAD_DIR / "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        # highest-resolution/highest-bitrate stream available, merged to
        # mp4 if the source has separate video/audio tracks; falls back to
        # a single best progressive stream (the common case for Instagram
        # and Twitter)
        "format": "bestvideo*+bestaudio/best",
        "format_sort": ["res", "fps", "vbr", "abr"],
        "merge_output_format": "mp4",
        # no re-encoding/postprocessing — whatever yt-dlp pulls down is
        # kept byte-for-byte so nothing degrades quality on the way in
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    cookies_path = _cookies_file_path(platform)
    if cookies_path:
        ydl_opts["cookiefile"] = str(cookies_path)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001
        raise DownloadError(f"failed to download {url}: {exc}") from exc

    # Twitter posts sometimes come back as a "playlist" of media entries
    # (multiple attached videos). When that happens yt-dlp puts each in
    # info["entries"] and info itself has no direct id/ext — pick the
    # first video entry.
    if info.get("_type") == "playlist" and info.get("entries"):
        info = info["entries"][0]

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
        platform=platform,
    )
