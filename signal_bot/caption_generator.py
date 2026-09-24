"""LLM-generated captions for reposted media.

Sends the downloaded media to Google's Gemini vision API and returns a
caption with a punchy hook line, an optional Europe-angle context
sentence, and a comment-farm question. Uses gemini-2.0-flash by
default, which is on Google's free tier (1500 requests/day).

Requires GEMINI_API_KEY (get one free at aistudio.google.com/apikey).
If it's not set, generate_caption() returns an empty string and the
caller falls back to a plain credit + hashtags caption — the pipeline
never fails on a missing API key.
"""

import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.0-flash"
MAX_INLINE_MEDIA_BYTES = 5 * 1024 * 1024  # 5MB per attachment
MAX_ATTACHMENTS = 6  # cap so multi-slide carousels don't blow the context

_SYSTEM_PROMPT = (
    "You write captions for Signal Europe, an Instagram account covering "
    "European venture capital, AI, and startup news. Voice: punchy, "
    "concise, curious. No hype. Facts over adjectives. Emojis only where "
    "they carry real meaning (flags, arrows) — otherwise none. Never "
    "invent numbers, names, or nationalities: if it isn't visible or "
    "clearly implied in the media, don't state it. Prefer a European "
    "angle when the content supports one; if it genuinely doesn't, don't "
    "force it — just skip the Europe line."
)

_USER_PROMPT = (
    "Write an Instagram caption for this post. Exact format:\n"
    "Line 1: hook headline, ≤90 characters, no trailing period. Punchy, "
    "specific.\n"
    "Blank line.\n"
    "Line 3: one sentence of context. Where possible, tie it to Europe — "
    "how this compares to Europe, why it matters for European founders/"
    "investors, or a European counterpart. Skip this line entirely if "
    "there's no honest European angle.\n"
    "Blank line.\n"
    "Last line: one question aimed at the audience of European "
    "founders/investors that invites a specific answer in the comments. "
    "Not rhetorical. Under 120 characters.\n\n"
    "Return only the caption text, nothing else. No hashtags — those are "
    "added separately."
)


class CaptionGenerationError(RuntimeError):
    pass


def _is_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


def _api_key() -> str:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""


def _extract_video_thumbnail(video_path: Path) -> Path | None:
    """Grabs a frame from ~1s into the video via ffmpeg so we can send
    something visual for video posts. Returns None if ffmpeg isn't
    installed or the extraction fails."""
    if shutil.which("ffmpeg") is None:
        return None
    fd, tmp = tempfile.mkstemp(prefix="thumb_", suffix=".jpg")
    os.close(fd)
    out = Path(tmp)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", "1",
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "3",
                str(out),
            ],
            check=True, timeout=30,
        )
    except Exception:  # noqa: BLE001
        out.unlink(missing_ok=True)
        return None
    return out if out.exists() and out.stat().st_size > 0 else None


def _collect_image_bytes(paths: list) -> tuple:
    """Loads image bytes (and mime type) for each usable path. Videos
    get a thumbnail extracted via ffmpeg first. Returns
    ([(bytes, mime), ...], [temp_paths_to_cleanup])."""
    entries: list = []
    temp_paths: list = []
    for path in paths[:MAX_ATTACHMENTS]:
        p = Path(path)
        if not p.exists():
            continue
        mime, _ = mimetypes.guess_type(str(p))
        if mime and mime.startswith("video/"):
            thumb = _extract_video_thumbnail(p)
            if thumb is None:
                continue
            temp_paths.append(thumb)
            p = thumb
            mime = "image/jpeg"
        if not mime or not mime.startswith("image/"):
            continue
        if p.stat().st_size > MAX_INLINE_MEDIA_BYTES:
            logger.info("caption_generator: skipping %s (%.1fMB > 5MB cap)",
                        p.name, p.stat().st_size / 1024 / 1024)
            continue
        entries.append((p.read_bytes(), mime))
    return entries, temp_paths


def _fallback_caption(_num_media: int) -> str:
    """Called when the LLM can't produce anything. Returns empty string
    so the caller falls back to credit + hashtags only."""
    return ""


def generate_caption(media_paths: list, hint: str = "") -> str:
    """Analyses the given media and returns a Signal Europe-flavoured
    caption. Returns "" if GEMINI_API_KEY isn't set or the call fails,
    letting the caller default to credit + hashtags."""
    if not _is_configured():
        return _fallback_caption(len(media_paths))

    try:
        # imported lazily so the dep is only required when this feature is used
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        logger.warning("caption_generator: google-genai not installed")
        return _fallback_caption(len(media_paths))

    image_entries, temp_paths = _collect_image_bytes(media_paths)
    try:
        if not image_entries and not hint:
            return _fallback_caption(len(media_paths))

        prompt = _USER_PROMPT
        if hint:
            prompt += (
                "\n\nSOURCE CAPTION (from the original poster — use it as "
                "background context to understand what the post is about, "
                "and to pull real names, numbers, and facts. DO NOT copy "
                "its phrasing verbatim, and DO NOT include the source "
                "poster's own CTAs like 'comment X', 'link in bio', "
                "'double-tap'. Write your caption in Signal Europe's own "
                "voice.):\n"
                f"{hint.strip()}"
            )

        # Build the multi-part input: images first, prompt last. Gemini
        # accepts bytes directly via types.Part.from_bytes().
        parts = [
            genai_types.Part.from_bytes(data=data, mime_type=mime)
            for data, mime in image_entries
        ]
        parts.append(prompt)

        client = genai.Client(api_key=_api_key())
        model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        try:
            response = client.models.generate_content(
                model=model,
                contents=parts,
                config=genai_types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    max_output_tokens=400,
                    temperature=0.7,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("caption_generator: Gemini API call failed")
            return _fallback_caption(len(media_paths))

        caption = (response.text or "").strip()
        return caption or _fallback_caption(len(media_paths))
    finally:
        for p in temp_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
