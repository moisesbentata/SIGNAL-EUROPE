"""LLM-generated captions for reposted media.

Sends the downloaded media to Anthropic's Claude vision API with a
Signal Europe-flavoured prompt and returns a caption with a punchy
hook line, a short context sentence tied to Europe when the content
allows, and a question at the end to encourage comments.

Requires ANTHROPIC_API_KEY. If it's not set, generate_caption() returns
an empty string and the caller falls back to a plain credit + hashtags
caption — the pipeline never fails on a missing API key.
"""

import base64
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"
MAX_INLINE_MEDIA_BYTES = 5 * 1024 * 1024  # 5MB per attachment to keep API happy
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
    return bool(os.getenv("ANTHROPIC_API_KEY"))


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


def _encode_media(paths: list) -> tuple:
    """Turns local file paths into Anthropic vision content blocks.
    For videos, grabs a thumbnail via ffmpeg. Returns (blocks, temp_paths)
    so the caller can clean up any extracted thumbnails when done."""
    blocks: list = []
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
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": data},
        })
    return blocks, temp_paths


def _fallback_caption(_num_media: int) -> str:
    """Called when the LLM can't produce anything (no API key, network
    failure, no visual content). Returns an empty string so the caller
    falls back to credit + hashtags only, rather than injecting a
    generic template that would feel canned."""
    return ""


def generate_caption(media_paths: list, hint: str = "") -> str:
    """Analyses the given media and returns a Signal Europe-flavoured
    caption (hook line, Europe-angle context sentence, one comment-farm
    question). Returns "" if the LLM isn't configured or fails, letting
    the caller default to credit + hashtags."""
    if not _is_configured():
        return _fallback_caption(len(media_paths))

    try:
        # imported lazily so the dep is only required when this feature is used
        from anthropic import Anthropic
    except ImportError:
        logger.warning("caption_generator: anthropic package not installed")
        return _fallback_caption(len(media_paths))

    blocks, temp_paths = _encode_media(media_paths)
    try:
        if not blocks and not hint:
            # nothing analysable AND no text hint — fall back rather than hallucinate
            return _fallback_caption(len(media_paths))

        prompt = _USER_PROMPT
        if hint:
            prompt += f"\n\nExtra context from the sender: {hint.strip()}"

        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
        try:
            response = client.messages.create(
                model=model,
                max_tokens=400,
                system=_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": blocks + [{"type": "text", "text": prompt}],
                }],
            )
        except Exception:  # noqa: BLE001
            logger.exception("caption_generator: API call failed")
            return _fallback_caption(len(media_paths))

        text_parts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
        caption = "\n".join(text_parts).strip()
        return caption or _fallback_caption(len(media_paths))
    finally:
        # clean up any thumbnails we extracted from videos
        for p in temp_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
