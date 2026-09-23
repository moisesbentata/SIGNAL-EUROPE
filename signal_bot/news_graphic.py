"""Renders a 1080x1920 (9:16 Reel format) branded news graphic PNG.

Original composition: deep navy background with a subtle top-to-bottom
gradient, a compact SGNL Europe wordmark top-right, a small yellow
"BREAKING" pill top-left, the news headline in large bold white type,
and a description paragraph in a muted grey below. Nothing here copies
any specific outside design — it's a straightforward news-card layout.

Fonts fall back to bundled DejaVu Sans (Bold/Regular) which supports €
and European accents; drop your real brand fonts at assets/fonts/ to
override. Similarly assets/logo/logo.png (transparent PNG, white/yellow
on transparent) replaces the programmatic logo when present.
"""

import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CANVAS_W = 1080
CANVAS_H = 1920

BG_TOP = (10, 14, 26)       # deep navy, top of canvas
BG_BOTTOM = (5, 7, 15)      # nearly black, bottom
YELLOW = (252, 238, 10)     # SGNL brand yellow
WHITE = (255, 255, 255)
MUTED = (160, 165, 180)
STAR_GRAY = (200, 202, 208)
LINE = (55, 60, 74)

FONTS_DIR = Path("assets/fonts")
BOLD_FONT_PATH = FONTS_DIR / "Bold.ttf"
REGULAR_FONT_PATH = FONTS_DIR / "Regular.ttf"
LOGO_PATH = Path("assets/logo/logo.png")


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _background() -> Image.Image:
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG_TOP)
    px = img.load()
    for y in range(CANVAS_H):
        t = y / (CANVAS_H - 1)
        r = round(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = round(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = round(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        for x in range(CANVAS_W):
            px[x, y] = (r, g, b)
    return img


def _star(draw, cx, cy, r_outer, color):
    r_inner = r_outer * 0.42
    pts = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(angle), cy - r * math.sin(angle)))
    draw.polygon(pts, fill=color)


def _draw_logo(img: Image.Image, x_right: int, y_top: int, height: int = 140) -> None:
    """Draws the SGNL EUROPE logo with top-right corner at (x_right, y_top)."""
    if LOGO_PATH.exists():
        logo = Image.open(LOGO_PATH).convert("RGBA")
        scale = height / logo.height
        logo = logo.resize((round(logo.width * scale), height))
        img.paste(logo, (x_right - logo.width, y_top), logo)
        return

    draw = ImageDraw.Draw(img)
    wordmark_font = _load_font(BOLD_FONT_PATH, round(height * 0.5))
    europe_font = _load_font(REGULAR_FONT_PATH, round(height * 0.18))

    wordmark = "SGNL"
    wordmark_w = draw.textlength(wordmark, font=wordmark_font)
    x = x_right - wordmark_w

    star_r = height * 0.09
    star_span = wordmark_w * 0.9
    star_start_x = x + (wordmark_w - star_span) / 2
    star_row_y = y_top + star_r
    for i in range(5):
        t = i / 4
        cx = star_start_x + t * star_span
        cy = star_row_y - math.sin(t * math.pi) * star_r * 1.6
        _star(draw, cx, cy, star_r, STAR_GRAY)

    wordmark_y = y_top + height * 0.32
    draw.text((x, wordmark_y), wordmark, font=wordmark_font, fill=WHITE)

    europe_text = "E U R O P E"
    europe_y = wordmark_y + height * 0.56
    europe_w = draw.textlength(europe_text, font=europe_font)
    draw.text((x + (wordmark_w - europe_w) / 2, europe_y), europe_text, font=europe_font, fill=YELLOW)


def _draw_pill(draw, xy, text, font, fg, bg, pad_x=28, pad_y=14):
    x, y = xy
    w = draw.textlength(text, font=font)
    ascent, descent = font.getmetrics()
    h = ascent + descent
    box = (x, y, x + w + 2 * pad_x, y + h + 2 * pad_y)
    draw.rounded_rectangle(box, radius=(h + 2 * pad_y) / 2, fill=bg)
    draw.text((x + pad_x, y + pad_y), text, font=font, fill=fg)


def _wrap(draw, text: str, font, max_width: int) -> list:
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


def _fit_headline(draw, text: str, max_width: int, max_lines: int,
                  start_size: int, min_size: int):
    """Picks the largest font size that fits `text` in `max_lines` lines
    within `max_width`. Returns (font, wrapped_lines)."""
    size = start_size
    while size >= min_size:
        font = _load_font(BOLD_FONT_PATH, size)
        lines = _wrap(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, lines, size
        size -= 4
    font = _load_font(BOLD_FONT_PATH, min_size)
    return font, _wrap(draw, text, font, max_width)[:max_lines], min_size


def render(headline: str, description: str, output_path: Path,
           badge_text: str = "BREAKING") -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img = _background()
    draw = ImageDraw.Draw(img)

    margin = 84
    content_width = CANVAS_W - 2 * margin

    # top row: badge left, logo right
    badge_font = _load_font(BOLD_FONT_PATH, 34)
    _draw_pill(draw, (margin, margin), badge_text.upper(),
               badge_font, fg=(15, 15, 20), bg=YELLOW)
    _draw_logo(img, x_right=CANVAS_W - margin, y_top=margin - 10, height=150)

    # thin divider under top row
    divider_y = margin + 140
    draw.line([(margin, divider_y), (CANVAS_W - margin, divider_y)],
              fill=LINE, width=2)

    # headline: auto-fit to occupy the upper-middle band with a
    # 4-line cap so a long headline shrinks rather than wraps forever
    headline_font, headline_lines, headline_size = _fit_headline(
        draw, headline, max_width=content_width, max_lines=4,
        start_size=110, min_size=58,
    )
    headline_line_h = round(headline_size * 1.12)

    # anchor the headline block roughly in the upper half of the canvas
    headline_y = divider_y + 140
    y = headline_y
    for line in headline_lines:
        draw.text((margin, y), line, font=headline_font, fill=WHITE)
        y += headline_line_h
    headline_end_y = y

    # description below headline
    if description:
        desc_font = _load_font(REGULAR_FONT_PATH, 44)
        desc_line_h = 60
        # available vertical space between headline and bottom brand mark
        bottom_reserve = 180  # for divider + brand mark
        available_h = CANVAS_H - bottom_reserve - (headline_end_y + 60)
        max_desc_lines = max(3, available_h // desc_line_h)
        desc_lines = _wrap(draw, description, desc_font, content_width)[:max_desc_lines]
        y = headline_end_y + 60
        for line in desc_lines:
            draw.text((margin, y), line, font=desc_font, fill=MUTED)
            y += desc_line_h

    # bottom brand mark
    bottom_line_y = CANVAS_H - 140
    draw.line([(margin, bottom_line_y), (CANVAS_W - margin, bottom_line_y)],
              fill=LINE, width=2)
    brand_font = _load_font(BOLD_FONT_PATH, 32)
    brand_text = "SIGNAL EUROPE"
    brand_w = draw.textlength(brand_text, font=brand_font)
    draw.text(((CANVAS_W - brand_w) / 2, bottom_line_y + 40),
              brand_text, font=brand_font, fill=YELLOW)

    img.save(output_path, format="PNG", optimize=True)
    return output_path


_URL_RE = re.compile(r"https?://\S+")


def parse_news_message(text: str) -> tuple:
    """Splits a NEWS message into (headline, description, source_url).
    Expected format:
        NEWS
        <headline line>
        <description, may span multiple lines>
        <optional url anywhere>
    URL is extracted and stripped from the body text.
    """
    body = re.sub(r"^\s*news\s*\n?", "", text, flags=re.IGNORECASE)
    url_match = _URL_RE.search(body)
    source_url = url_match.group(0) if url_match else ""
    if url_match:
        body = (body[: url_match.start()] + body[url_match.end():]).strip()

    lines = [ln.strip() for ln in body.strip().splitlines() if ln.strip()]
    if not lines:
        return "", "", source_url
    headline = lines[0]
    description = " ".join(lines[1:]).strip()
    return headline, description, source_url
