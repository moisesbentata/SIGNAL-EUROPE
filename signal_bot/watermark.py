"""Brands downloaded media with the Signal Europe watermark.

Used by the /format command: take a source post's media (photo, video,
or a whole carousel) and stamp our mark onto every item *in place of*
reposting it raw — so the result looks like our own content while
keeping the original post's shape (a carousel stays a carousel, a video
stays a video, order preserved).

The watermark is a corner badge: a semi-transparent dark rounded pill
with a small yellow star and the "SIGNAL EUROPE" wordmark in the brand
yellow. The dark scrim behind it keeps the text legible on any
background, light or dark. If a real logo is dropped at
assets/logo/logo.png (transparent PNG) it's used top-right in addition
to the corner wordmark.

  * Images are composited with Pillow.
  * Videos are re-encoded with ffmpeg, overlaying a full-frame
    transparent PNG whose branding is baked into the corner.

Both paths are best-effort in the sense that the caller decides what to
do on failure, but they raise WatermarkError on a hard failure so the
caller can surface it rather than silently posting un-branded media.
"""

import logging
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

YELLOW = (252, 238, 10, 255)
WHITE = (255, 255, 255, 255)
SCRIM = (10, 14, 26, 170)  # translucent navy behind the wordmark

FONTS_DIR = Path("assets/fonts")
BOLD_FONT_PATH = FONTS_DIR / "Bold.ttf"
LOGO_PATH = Path("assets/logo/logo.png")

WORDMARK = "SIGNAL EUROPE"


class WatermarkError(RuntimeError):
    pass


def _load_font(size: int):
    from PIL import ImageFont

    if BOLD_FONT_PATH.exists():
        return ImageFont.truetype(str(BOLD_FONT_PATH), size)
    return ImageFont.load_default(size=size)


def _star(draw, cx, cy, r_outer, color):
    r_inner = r_outer * 0.42
    pts = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(angle), cy - r * math.sin(angle)))
    draw.polygon(pts, fill=color)


def _render_overlay(width: int, height: int):
    """Builds a transparent RGBA overlay the size of the media with the
    branding baked into the bottom-right corner (plus the logo top-right
    if assets/logo/logo.png exists). Returns a Pillow Image."""
    from PIL import Image, ImageDraw

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # scale everything off the shorter edge so portrait and landscape
    # both get a sensibly-sized mark
    base = min(width, height)
    font_size = max(int(base * 0.045), 20)
    font = _load_font(font_size)

    text_w = draw.textlength(WORDMARK, font=font)
    ascent, descent = font.getmetrics()
    text_h = ascent + descent

    pad_x = int(font_size * 0.7)
    pad_y = int(font_size * 0.45)
    star_r = font_size * 0.55
    star_gap = int(font_size * 0.5)

    pill_w = text_w + 2 * pad_x + star_r * 2 + star_gap
    pill_h = text_h + 2 * pad_y
    margin = int(base * 0.04)

    x1 = width - margin - pill_w
    y1 = height - margin - pill_h
    x2 = width - margin
    y2 = height - margin

    draw.rounded_rectangle([x1, y1, x2, y2], radius=pill_h / 2, fill=SCRIM)

    star_cx = x1 + pad_x + star_r
    star_cy = (y1 + y2) / 2
    _star(draw, star_cx, star_cy, star_r, YELLOW)

    text_x = star_cx + star_r + star_gap
    text_y = y1 + pad_y - descent / 2
    draw.text((text_x, text_y), WORDMARK, font=font, fill=YELLOW)

    if LOGO_PATH.exists():
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA")
            target_h = int(base * 0.09)
            scale = target_h / logo.height
            logo = logo.resize((max(1, round(logo.width * scale)), target_h))
            overlay.alpha_composite(logo, (width - margin - logo.width, margin))
        except Exception:  # noqa: BLE001 - logo is optional, never fatal
            logger.exception("watermark: failed to place logo.png, skipping it")

    return overlay


def watermark_image(src: Path) -> Path:
    """Stamps the watermark on an image. Returns the path to a new
    JPEG file (the original is left untouched)."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise WatermarkError("Pillow is not installed") from exc

    try:
        img = Image.open(src).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise WatermarkError(f"could not open image {src.name}: {exc}") from exc

    overlay = _render_overlay(img.width, img.height)
    img.alpha_composite(overlay)

    out = src.with_name(f"{src.stem}_wm.jpg")
    img.convert("RGB").save(out, format="JPEG", quality=92, optimize=True)
    return out


def _probe_dimensions(src: Path) -> tuple:
    """Returns (width, height) of a video via ffprobe, or (0, 0) if it
    can't be determined."""
    if shutil.which("ffprobe") is None:
        return 0, 0
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x",
                str(src),
            ],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        w, h = out.split("x")
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return 0, 0


def watermark_video(src: Path, width: int = 0, height: int = 0) -> Path:
    """Stamps the watermark on a video by overlaying a full-frame PNG and
    re-encoding with ffmpeg. Returns the path to a new MP4. width/height
    may be passed from the downloader's MediaItem to skip an ffprobe
    call; if they're unknown we probe."""
    if shutil.which("ffmpeg") is None:
        raise WatermarkError("ffmpeg is not installed")

    if not width or not height:
        width, height = _probe_dimensions(src)
    if not width or not height:
        raise WatermarkError(f"could not determine dimensions of {src.name}")

    overlay = _render_overlay(width, height)
    fd, overlay_png = tempfile.mkstemp(prefix="wm_overlay_", suffix=".png")
    os.close(fd)
    overlay.save(overlay_png, format="PNG")

    out = src.with_name(f"{src.stem}_wm.mp4")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(src),
                "-i", overlay_png,
                "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", "veryfast", "-crf", "20",
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(out),
            ],
            check=True, capture_output=True, text=True, timeout=300,
        )
    except subprocess.CalledProcessError as exc:
        # audio "copy" fails on some containers/codecs — retry re-encoding
        # audio to AAC, which is broadly compatible with IG anyway
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(src),
                    "-i", overlay_png,
                    "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart",
                    str(out),
                ],
                check=True, capture_output=True, text=True, timeout=300,
            )
        except subprocess.CalledProcessError as exc2:
            detail = (exc2.stderr or exc.stderr or "").strip()[-500:]
            raise WatermarkError(f"ffmpeg overlay failed: {detail}") from exc2
    finally:
        Path(overlay_png).unlink(missing_ok=True)

    if not out.exists() or out.stat().st_size == 0:
        raise WatermarkError("ffmpeg produced an empty output file")
    return out


def apply(item) -> Path:
    """Watermarks a downloader.MediaItem based on its media_type and
    returns the path to the new (branded) file. The original file is left
    in place for the caller to clean up."""
    if item.media_type == "image":
        return watermark_image(item.local_path)
    return watermark_video(item.local_path, item.width, item.height)
