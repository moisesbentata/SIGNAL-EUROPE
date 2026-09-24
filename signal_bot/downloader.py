"""Downloads media from a public Instagram or Twitter/X URL using
yt-dlp, and extracts enough metadata to credit the original creator in
the repost caption.

Handles three shapes of source content:
  - single video (IG reel, tweet with one video attached)
  - single image (IG single-photo post)  [experimental]
  - carousel (IG post with 2-10 mixed image/video items)

All are collapsed onto the same DownloadedPost shape: a list of
MediaItem entries, each with a local file path and a media_type
("image" or "video"). Callers can branch on len(items) == 1 vs > 1 to
choose the single-post vs carousel publishing flow.

Both platforms are covered by yt-dlp out of the box but both are
increasingly gated. Set IG_COOKIES_B64 / TWITTER_COOKIES_B64 (base64
of a cookies.txt from a throwaway account) to pass credentials to
yt-dlp when anonymous fetches fail. Both are optional.

Using logged-in cookies to fetch other people's public posts is against
both platforms' terms, so throwaway accounts only — never your real
posting account.
"""

import base64
import os
import re
import tempfile
from dataclasses import dataclass, field
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

_COOKIE_ENV = {
    "instagram": "IG_COOKIES_B64",
    "twitter": "TWITTER_COOKIES_B64",
}
_COOKIES_TMP_PATHS: dict = {}

# Instagram Graph API's own limits for feed carousel items published via
# {image,video}_url — verify current values at developers.facebook.com
# before relying on them. Checked after download so oversized source
# files fail loudly rather than silently rejected by the API.
GRAPH_API_MAX_VIDEO_BYTES = 100 * 1024 * 1024   # 100 MB per video
GRAPH_API_MAX_IMAGE_BYTES = 8 * 1024 * 1024      # 8 MB per image

# Extensions yt-dlp emits for image-only IG posts. Anything not in here
# is treated as video (yt-dlp's default output for reels/tweets is mp4).
_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "heic"}


class DownloadError(RuntimeError):
    pass


@dataclass
class MediaItem:
    local_path: Path
    media_type: str  # "image" or "video"
    width: int = 0   # 0 = unknown; caller falls back to Telegram/IG defaults
    height: int = 0  # so previews and IG uploads can preserve aspect ratio
    duration: float = 0.0  # video only, seconds

    @property
    def size_bytes(self) -> int:
        return self.local_path.stat().st_size

    @property
    def exceeds_limit(self) -> bool:
        limit = GRAPH_API_MAX_VIDEO_BYTES if self.media_type == "video" else GRAPH_API_MAX_IMAGE_BYTES
        return self.size_bytes > limit


@dataclass
class DownloadedPost:
    items: list  # list[MediaItem], always length >= 1
    caption: str  # original poster's caption (we ignore in caption builder)
    owner_username: str
    source_url: str
    platform: str  # "instagram" or "twitter"

    @property
    def is_carousel(self) -> bool:
        return len(self.items) > 1

    @property
    def is_video_only(self) -> bool:
        return all(item.media_type == "video" for item in self.items)

    @property
    def post_type_hint(self) -> str:
        """Short human-readable label for status messages ("reel",
        "photo", "3-video carousel", etc)."""
        if len(self.items) == 1:
            return "reel" if self.items[0].media_type == "video" else "photo"
        all_photos = all(i.media_type == "image" for i in self.items)
        all_videos = all(i.media_type == "video" for i in self.items)
        if all_photos:
            return f"{len(self.items)}-photo carousel"
        if all_videos:
            return f"{len(self.items)}-video carousel"
        return f"{len(self.items)}-item mixed carousel"

    def oversized_items(self) -> list:
        return [item for item in self.items if item.exceeds_limit]


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


def _resolve_path(ydl: yt_dlp.YoutubeDL, info: dict) -> Path | None:
    """Best-effort resolve the actual downloaded file path from a
    yt-dlp info dict. yt-dlp sometimes remuxes to a different extension
    than it originally advertised."""
    predicted = Path(ydl.prepare_filename(info))
    if predicted.exists():
        return predicted
    entry_id = info.get("id")
    if not entry_id:
        return None
    candidates = list(DOWNLOAD_DIR.glob(f"{entry_id}.*"))
    return candidates[0] if candidates else None


def _entry_has_video(entry: dict) -> bool:
    """True when the entry has at least one downloadable video track.
    IG image-only carousel slides come through with no video formats."""
    for f in entry.get("formats") or []:
        if f.get("vcodec") not in (None, "none"):
            return True
    # some IG entries put the video info in `url` + `ext` at the top level
    if entry.get("ext") and entry["ext"].lower() not in _IMAGE_EXTS:
        return True
    return False


def _fetch_entry(entry: dict, ydl_opts: dict) -> MediaItem | None:
    """Downloads a single entry — via yt-dlp for videos, via requests
    for image-only entries — and wraps it in a MediaItem. Returns None
    (silently skipping) on any failure so a partly-broken carousel
    still yields whatever slides did work."""
    entry_id = entry.get("id") or ""
    entry_url = entry.get("webpage_url") or entry.get("url") or ""

    if _entry_has_video(entry) and entry_url:
        # Video: let yt-dlp do the actual download. We use extract_info
        # per entry rather than ydl.download() so we get back a full
        # info dict we can resolve the file path from.
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(entry_url, download=True)
                if info is None:
                    return None
                if info.get("_type") == "playlist" and info.get("entries"):
                    info = info["entries"][0]
                path = _resolve_path(ydl, info)
        except Exception:  # noqa: BLE001 - one bad slide shouldn't sink the rest
            return None
        if path is None:
            return None
        ext = path.suffix.lstrip(".").lower()
        media_type = "image" if ext in _IMAGE_EXTS else "video"
        return MediaItem(
            local_path=path,
            media_type=media_type,
            width=int(info.get("width") or entry.get("width") or 0),
            height=int(info.get("height") or entry.get("height") or 0),
            duration=float(info.get("duration") or entry.get("duration") or 0),
        )

    # Image-only entry: pull the URL out of a handful of fields
    # different yt-dlp versions populate, then download via requests.
    image_url = _entry_image_url(entry)
    if not image_url:
        return None
    path = _download_image_via_requests(image_url, entry_id or "img")
    if path is None:
        return None
    return MediaItem(
        local_path=path,
        media_type="image",
        width=int(entry.get("width") or 0),
        height=int(entry.get("height") or 0),
    )


def _normalize_url(url: str, platform: str) -> str:
    """Strips query params that trip up yt-dlp. For Instagram: img_index
    selects a specific carousel slide (we want the whole carousel), and
    stkn is a session-scoped token that yt-dlp doesn't need."""
    if platform == "instagram":
        m = re.match(
            r"(https?://(?:www\.)?instagram\.com/(?:p|reel|reels)/[A-Za-z0-9_-]+)/?",
            url, re.IGNORECASE,
        )
        if m:
            return m.group(1) + "/"
    return url


def _download_image_via_requests(image_url: str, entry_id: str) -> Path | None:
    """Fallback when yt-dlp couldn't materialise an image entry. Fetches
    the URL directly and saves under DOWNLOAD_DIR."""
    import requests
    try:
        resp = requests.get(image_url, timeout=30, stream=True)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001
        return None
    # infer extension from content-type or fall back to .jpg
    ctype = resp.headers.get("content-type", "").lower()
    if "png" in ctype:
        ext = "png"
    elif "webp" in ctype:
        ext = "webp"
    else:
        ext = "jpg"
    out = DOWNLOAD_DIR / f"{entry_id}.{ext}"
    with open(out, "wb") as f:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if chunk:
                f.write(chunk)
    return out if out.stat().st_size > 0 else None


def _entry_image_url(entry: dict) -> str:
    """Digs a usable image URL out of an entry that yt-dlp couldn't
    format-select (image-only IG posts). Checks a handful of fields
    that different yt-dlp versions populate."""
    for key in ("url", "display_url"):
        val = entry.get(key)
        if val and isinstance(val, str):
            return val
    thumbs = entry.get("thumbnails") or []
    # thumbnails is a list of {url, width, height} — pick the largest
    if thumbs:
        best = max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
        if best.get("url"):
            return best["url"]
    return ""


def download(url: str) -> DownloadedPost:
    platform = detect_platform(url)
    url = _normalize_url(url, platform)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(DOWNLOAD_DIR / "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        # highest-resolution/highest-bitrate stream available, merged to
        # mp4 if the source has separate tracks
        "format": "bestvideo*+bestaudio/best",
        "format_sort": ["res", "fps", "vbr", "abr"],
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        # allow playlists so IG carousels download every item, not just
        # the first
        "noplaylist": False,
        # keep going if one entry in a carousel fails so we still get
        # the rest
        "ignoreerrors": True,
    }

    cookies_path = _cookies_file_path(platform)
    if cookies_path:
        ydl_opts["cookiefile"] = str(cookies_path)

    # Two-phase: first extract info without downloading so we can
    # decide per-entry how to fetch it (yt-dlp for videos, requests
    # for image-only entries). This survives IG posts that are
    # image-only, which the video-format selector otherwise chokes on.
    try:
        with yt_dlp.YoutubeDL({**ydl_opts, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise DownloadError(f"failed to extract info from {url}: {exc}") from exc

    if info is None:
        raise DownloadError(f"yt-dlp returned no info for {url}")

    entries = info.get("entries") if info.get("_type") == "playlist" else [info]
    entries = [e for e in entries if e]
    if not entries:
        raise DownloadError(f"no media found at {url}")

    items = []
    for entry in entries:
        item = _fetch_entry(entry, ydl_opts)
        if item is not None:
            items.append(item)

    if not items:
        raise DownloadError(f"couldn't download any media from {url}")

    # metadata (caption / uploader) comes from the top-level info for
    # playlists, from the entry itself for singletons
    meta_source = info if info.get("_type") == "playlist" else entries[0]
    caption = (meta_source.get("description") or entries[0].get("description") or "").strip()
    owner = _extract_handle(meta_source) or _extract_handle(entries[0])

    return DownloadedPost(
        items=items,
        caption=caption,
        owner_username=owner,
        source_url=url,
        platform=platform,
    )


def _extract_handle(info: dict) -> str:
    """Best-effort extraction of a platform @handle from a yt-dlp info
    dict. Instagram sometimes returns the numeric user id in
    `uploader_id` (e.g. 55894943966) instead of the actual @handle, so
    we prefer the last path segment of `uploader_url` when available.

    Fallback order: uploader_url path → channel → uploader_id →
    uploader — but only when the candidate isn't all-digits.
    """
    url = info.get("uploader_url") or info.get("channel_url") or ""
    if url:
        # trailing-slash-safe last segment
        path = url.rstrip("/").rsplit("/", 1)[-1]
        if path and not path.isdigit():
            return path
    for key in ("channel", "uploader_id", "uploader"):
        val = (info.get(key) or "").strip()
        if val and not val.isdigit():
            return val
    return ""
