"""Renders a SourceItem into a branded 1080x1080 Signal Europe graphic.

Deliberately does not touch any image from the original source — only
the extracted headline/summary text is used, drawn onto our own template.
Drop brand fonts into assets/fonts/ (Bold.ttf / Regular.ttf) to replace
the generic fallback used when no custom font is present.
"""

import os
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from signal_europe.models import SourceItem

CANVAS_SIZE = (1080, 1080)
BG_COLOR = (12, 14, 20)          # near-black navy
ACCENT_COLOR = (94, 234, 212)    # teal accent
TEXT_COLOR = (245, 245, 245)
MUTED_COLOR = (150, 156, 168)

FONTS_DIR = Path("assets/fonts")
BOLD_FONT_PATH = FONTS_DIR / "Bold.ttf"
REGULAR_FONT_PATH = FONTS_DIR / "Regular.ttf"


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list:
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render(item: SourceItem, output_dir: Path = Path("output/review_queue")) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", CANVAS_SIZE, BG_COLOR)
    draw = ImageDraw.Draw(img)

    margin = 72
    content_width = CANVAS_SIZE[0] - 2 * margin

    # top kicker / brand strip
    kicker_font = _load_font(REGULAR_FONT_PATH, 30)
    draw.text((margin, 64), "SIGNAL EUROPE", font=kicker_font, fill=ACCENT_COLOR)
    tag = (item.tags[0] if item.tags else "").upper()
    if tag:
        tag_font = _load_font(REGULAR_FONT_PATH, 24)
        tag_w = draw.textlength(tag, font=tag_font)
        draw.text((CANVAS_SIZE[0] - margin - tag_w, 70), tag, font=tag_font, fill=MUTED_COLOR)

    draw.line([(margin, 118), (CANVAS_SIZE[0] - margin, 118)], fill=(40, 44, 54), width=2)

    # headline
    headline_font = _load_font(BOLD_FONT_PATH, 58)
    headline_lines = _wrap(draw, item.headline, headline_font, content_width)[:5]
    y = 220
    for line in headline_lines:
        draw.text((margin, y), line, font=headline_font, fill=TEXT_COLOR)
        y += 70

    # summary
    if item.summary:
        summary_font = _load_font(REGULAR_FONT_PATH, 32)
        summary_lines = _wrap(draw, item.summary, summary_font, content_width)[:6]
        y += 30
        for line in summary_lines:
            draw.text((margin, y), line, font=summary_font, fill=MUTED_COLOR)
            y += 44

    # footer
    footer_font = _load_font(REGULAR_FONT_PATH, 26)
    footer_text = "European VC & AI, daily"
    draw.text((margin, CANVAS_SIZE[1] - 90), footer_text, font=footer_font, fill=ACCENT_COLOR)

    safe_id = "".join(c if c.isalnum() else "_" for c in item.item_id)[:60]
    out_path = output_dir / f"{item.source.replace(':', '_')}__{safe_id}.png"
    img.save(out_path)
    return out_path
