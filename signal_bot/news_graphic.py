"""Renders a 1080x1920 (9:16 Reel format) branded news graphic PNG.

The look is controlled by `assets/branding.json` (see signal_bot/
branding.py) so an operator can make it *their* format without editing
code:

  * Drop a 1080x1920 background you designed in Figma/Canva at
    `assets/template/news_bg.png` (or whatever path you set in
    branding.json → news.background_image) and it's used as the
    backdrop — bake in your logo, colours and decoration, leaving open
    space for the headline/description text.
  * Tune text boxes, colours, the badge, and the brand marks via
    branding.json → news.*

With no branding.json and no background image, it falls back to the
original composition: deep-navy gradient, SGNL Europe wordmark top-right,
yellow "BREAKING" pill, white headline, muted description.

Fonts fall back to bundled DejaVu Sans (Bold/Regular) which supports €
and European accents; drop your own brand fonts at assets/fonts/
Bold.ttf and Regular.ttf to override. assets/logo/logo.png (transparent
PNG) replaces the programmatic logo when present.
"""

import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from signal_bot import branding

CANVAS_W = 1080
CANVAS_H = 1920

BG_TOP = (10, 14, 26)       # deep navy, top of canvas
BG_BOTTOM = (5, 7, 15)      # nearly black, bottom
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


def _hex(value, default) -> tuple:
    if not value:
        return default
    return branding.hex_to_rgb(value, default)


def _background() -> Image.Image:
    """Operator's own 1080x1920 background if they dropped one in (per
    branding.json → news.background_image); otherwise the built-in navy
    gradient."""
    cfg = branding.news()
    bg_path = Path(cfg.get("background_image", "") or "")
    if bg_path.exists():
        try:
            img = Image.open(bg_path).convert("RGB")
            if img.size != (CANVAS_W, CANVAS_H):
                img = img.resize((CANVAS_W, CANVAS_H))
            return img
        except Exception:  # noqa: BLE001 - fall back to the generated bg
            pass
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


def _has_custom_background() -> bool:
    bg_path = Path(branding.news().get("background_image", "") or "")
    return bg_path.exists()


def _star(draw, cx, cy, r_outer, color):
    r_inner = r_outer * 0.42
    pts = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(angle), cy - r * math.sin(angle)))
    draw.polygon(pts, fill=color)


def _draw_logo(img: Image.Image, x_right: int, y_top: int, height: int = 140) -> None:
    """Draws the top-right brand mark. Prefers assets/logo/logo.png.
    With no logo file and the default Signal brand, draws the stylised
    SGNL/EUROPE wordmark; with a custom brand_name, draws that name
    plainly in the accent colour."""
    if LOGO_PATH.exists():
        logo = Image.open(LOGO_PATH).convert("RGBA")
        scale = height / logo.height
        logo = logo.resize((round(logo.width * scale), height))
        img.paste(logo, (x_right - logo.width, y_top), logo)
        return

    accent = branding.accent_rgb()
    white = _hex(branding.news().get("text_color"), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Custom brand (not the Signal default) with no logo file: a plain
    # wordmark in the accent colour, right-aligned.
    if branding.brand_name().strip().upper() != "SIGNAL EUROPE":
        wm_font = _load_font(BOLD_FONT_PATH, round(height * 0.42))
        name = branding.brand_name().upper()
        w = draw.textlength(name, font=wm_font)
        draw.text((x_right - w, y_top + height * 0.3), name, font=wm_font, fill=accent)
        return

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
    draw.text((x, wordmark_y), wordmark, font=wordmark_font, fill=white)

    europe_text = "E U R O P E"
    europe_y = wordmark_y + height * 0.56
    europe_w = draw.textlength(europe_text, font=europe_font)
    draw.text((x + (wordmark_w - europe_w) / 2, europe_y), europe_text, font=europe_font, fill=accent)


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

    cfg = branding.news()
    accent = branding.accent_rgb()
    text_color = _hex(cfg.get("text_color"), (255, 255, 255))
    muted_color = _hex(cfg.get("muted_color"), (160, 165, 180))
    custom_bg = _has_custom_background()

    img = _background()
    draw = ImageDraw.Draw(img)

    margin = 84
    content_width = CANVAS_W - 2 * margin

    # --- top row: badge (left) + logo (right) ---
    badge_cfg = cfg.get("badge", {}) or {}
    if badge_cfg.get("enabled", True):
        badge_font = _load_font(BOLD_FONT_PATH, 34)
        _draw_pill(
            draw,
            (int(badge_cfg.get("x", margin)), int(badge_cfg.get("y", margin))),
            str(badge_cfg.get("text", badge_text)).upper(),
            badge_font, fg=(15, 15, 20), bg=accent,
        )
    if cfg.get("show_top_logo", True):
        _draw_logo(img, x_right=CANVAS_W - margin, y_top=margin - 10, height=150)

    # On the generated background we draw dividers; on a custom template
    # we don't, so we never paint lines over the operator's own design.
    if not custom_bg:
        divider_y = margin + 140
        draw.line([(margin, divider_y), (CANVAS_W - margin, divider_y)], fill=LINE, width=2)

    # --- headline (auto-fit) ---
    h_cfg = cfg.get("headline", {}) or {}
    h_x = int(h_cfg.get("x", margin))
    h_width = int(h_cfg.get("width", content_width))
    headline_font, headline_lines, headline_size = _fit_headline(
        draw, headline, max_width=h_width,
        max_lines=int(h_cfg.get("max_lines", 4)),
        start_size=int(h_cfg.get("size_max", 110)),
        min_size=int(h_cfg.get("size_min", 58)),
    )
    headline_line_h = round(headline_size * 1.12)
    y = int(h_cfg.get("y", (margin + 140) + 140))
    for line in headline_lines:
        draw.text((h_x, y), line, font=headline_font, fill=text_color)
        y += headline_line_h
    headline_end_y = y

    # --- description below headline ---
    if description:
        d_cfg = cfg.get("description", {}) or {}
        d_x = int(d_cfg.get("x", margin))
        d_width = int(d_cfg.get("width", content_width))
        desc_font = _load_font(REGULAR_FONT_PATH, int(d_cfg.get("size", 44)))
        desc_line_h = int(d_cfg.get("line_height", 60))
        gap = int(d_cfg.get("gap", 60))
        bottom_reserve = 180
        available_h = CANVAS_H - bottom_reserve - (headline_end_y + gap)
        max_desc_lines = max(3, available_h // desc_line_h)
        desc_lines = _wrap(draw, description, desc_font, d_width)[:max_desc_lines]
        y = headline_end_y + gap
        for line in desc_lines:
            draw.text((d_x, y), line, font=desc_font, fill=muted_color)
            y += desc_line_h

    # --- bottom brand mark ---
    if cfg.get("show_bottom_brand", True):
        bottom_line_y = CANVAS_H - 140
        if not custom_bg:
            draw.line([(margin, bottom_line_y), (CANVAS_W - margin, bottom_line_y)],
                      fill=LINE, width=2)
        brand_font = _load_font(BOLD_FONT_PATH, 32)
        brand_text = branding.brand_name().upper()
        brand_w = draw.textlength(brand_text, font=brand_font)
        draw.text(((CANVAS_W - brand_w) / 2, bottom_line_y + 40),
                  brand_text, font=brand_font, fill=accent)

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
