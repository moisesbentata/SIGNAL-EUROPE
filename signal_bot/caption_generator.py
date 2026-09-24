"""LLM-generated captions for media the user sends directly.

Called when a photo/video (or a media group of them) arrives in Telegram
without a source URL. Sends the media to Anthropic's Claude vision API
with a Signal Europe-flavoured prompt and returns a short, punchy hook
caption plus a slightly longer explanation.

Requires ANTHROPIC_API_KEY. If it's not set, generate_caption() falls
back to a static template so the pipeline still works — the user just
has to write captions manually then.
"""

import base64
import logging
import mimetypes
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"
MAX_INLINE_MEDIA_BYTES = 5 * 1024 * 1024  # 5MB per attachment to keep API happy
MAX_ATTACHMENTS = 6  # cap so multi-slide carousels don't blow the context

_SYSTEM_PROMPT = (
    "You write captions for Signal Europe, an Instagram account covering "
    "European venture capital, AI, and startup news. Voice: punchy, "
    "concise, no fluff, no hype, no emojis. Facts over adjectives. "
    "Never invent numbers or names — if a fact isn't visible or implied "
    "in the media, don't state it."
)

_USER_PROMPT = (
    "Write a caption for this post. Format exactly:\n"
    "Line 1: a hook headline, max 90 characters, no trailing period.\n"
    "Blank line.\n"
    "Line 3+: 1-2 sentences of context that a reader who saw only the "
    "media would want to know. Under 250 characters total.\n\n"
    "Return only the caption text, nothing else."
)


class CaptionGenerationError(RuntimeError):
    pass


def _is_configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _fallback_caption(num_media: int) -> str:
    if num_media > 1:
        return "A signal from European tech."
    return "A signal from European tech."


def _encode_media(paths: list) -> list:
    """Turns local file paths into Anthropic vision content blocks.
    Skips anything larger than MAX_INLINE_MEDIA_BYTES (the model would
    reject or truncate them anyway) and anything past MAX_ATTACHMENTS."""
    blocks = []
    for path in paths[:MAX_ATTACHMENTS]:
        p = Path(path)
        if not p.exists():
            continue
        if p.stat().st_size > MAX_INLINE_MEDIA_BYTES:
            logger.info("caption_generator: skipping %s (%.1fMB > 5MB cap)",
                        p.name, p.stat().st_size / 1024 / 1024)
            continue
        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            continue
        if mime.startswith("image/"):
            data = base64.b64encode(p.read_bytes()).decode("ascii")
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            })
        # Anthropic vision doesn't accept raw video in the Messages API
        # yet; for video-only posts we send the prompt with no media
        # and rely on the fallback text below.
    return blocks


def generate_caption(media_paths: list, hint: str = "") -> str:
    """Analyses the given media and returns a Signal Europe-flavoured
    caption. Falls back to a static template if the LLM isn't configured
    or errors out — the bot always gets a usable string back."""
    if not _is_configured():
        return _fallback_caption(len(media_paths))

    try:
        # imported lazily so the dep is only required when this feature is used
        from anthropic import Anthropic
    except ImportError:
        logger.warning("caption_generator: anthropic package not installed")
        return _fallback_caption(len(media_paths))

    blocks = _encode_media(media_paths)
    if not blocks:
        # nothing analysable — fall back rather than hallucinate
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
    except Exception as exc:  # noqa: BLE001
        logger.exception("caption_generator: API call failed")
        return _fallback_caption(len(media_paths))

    text_parts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
    caption = "\n".join(text_parts).strip()
    if not caption:
        return _fallback_caption(len(media_paths))
    return caption
