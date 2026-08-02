"""Renders a SourceItem into one or more branded 1080x1080 Signal Europe
slides, matching the SGNL Europe post template: black-to-white-to-black
vertical gradient, a small stars-arc/wordmark logo in the top-left, an
optional "Breaking News" pill + circular company badge on the opening
slide, a bold multi-line headline with yellow-highlighted key terms, and
a "Read More" pill button.

Deliberately does not touch any image from the original source — only
extracted text is drawn onto our own template. Drop brand fonts into
assets/fonts/ (Bold.ttf / Regular.ttf) to replace the bundled fallback,
and drop the real logo file into assets/logo/logo.png (transparent,
white/yellow-on-transparent) to replace the programmatic approximation
drawn by draw_logo() below.
"""

import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from signal_europe.models import SourceItem

CANVAS_SIZE = (1080, 1080)

BLACK = (10, 10, 12)
WHITE = (255, 255, 255)
YELLOW = (250, 224, 0)
MUTED = (150, 156, 168)
PILL_BG = (55, 57, 62)
STAR_GRAY = (200, 202, 208)

FONTS_DIR = Path("assets/fonts")
BOLD_FONT_PATH = FONTS_DIR / "Bold.ttf"
REGULAR_FONT_PATH = FONTS_DIR / "Regular.ttf"

LOGO_PATH = Path("assets/logo/logo.png")  # optional real logo, transparent PNG


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


# ---------------------------------------------------------------- background

def _vertical_gradient(size, stops):
    """stops: list of (fraction 0..1, (r,g,b)) sorted by fraction."""
    width, height = size
    img = Image.new("RGB", size, stops[0][1])
    px = img.load()
    row_colors = []
    for y in range(height):
        frac = y / (height - 1)
        for i in range(len(stops) - 1):
            f0, c0 = stops[i]
            f1, c1 = stops[i + 1]
            if f0 <= frac <= f1:
                t = 0 if f1 == f0 else (frac - f0) / (f1 - f0)
                r = round(c0[0] + (c1[0] - c0[0]) * t)
                g = round(c0[1] + (c1[1] - c0[1]) * t)
                b = round(c0[2] + (c1[2] - c0[2]) * t)
                row_colors.append((r, g, b))
                break
        else:
            row_colors.append(stops[-1][1])
    for y, color in enumerate(row_colors):
        for x in range(width):
            px[x, y] = color
    return img


def _background(with_light_band: bool = True) -> Image.Image:
    if with_light_band:
        stops = [
            (0.0, BLACK),
            (0.18, BLACK),
            (0.38, WHITE),
            (0.50, WHITE),
            (0.62, BLACK),
            (1.0, BLACK),
        ]
    else:
        stops = [(0.0, BLACK), (1.0, BLACK)]
    return _vertical_gradient(CANVAS_SIZE, stops)


# ---------------------------------------------------------------- logo

def _star(draw: ImageDraw.ImageDraw, cx: float, cy: float, r_outer: float, color):
    r_inner = r_outer * 0.42
    points = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        points.append((cx + r * math.cos(angle), cy - r * math.sin(angle)))
    draw.polygon(points, fill=color)


def draw_logo(img: Image.Image, x: int, y: int, target_height: int = 100) -> int:
    """Draws the SGNL Europe logo with top-left corner at (x, y).
    Uses the real logo file if present, otherwise a programmatic
    approximation (stars arc + SGNL wordmark + EUROPE). Returns the
    logo's rendered width.
    """
    if LOGO_PATH.exists():
        logo = Image.open(LOGO_PATH).convert("RGBA")
        scale = target_height / logo.height
        logo = logo.resize((round(logo.width * scale), target_height))
        img.paste(logo, (x, y), logo)
        return logo.width

    draw = ImageDraw.Draw(img)
    star_r = target_height * 0.09
    star_row_y = y + star_r
    wordmark_font = _load_font(BOLD_FONT_PATH, round(target_height * 0.5))
    europe_font = _load_font(REGULAR_FONT_PATH, round(target_height * 0.18))

    wordmark = "SGNL"
    wordmark_w = draw.textlength(wordmark, font=wordmark_font)
    star_span = wordmark_w * 0.9
    star_start_x = x + (wordmark_w - star_span) / 2

    for i in range(5):
        t = i / 4
        cx = star_start_x + t * star_span
        cy = star_row_y - math.sin(t * math.pi) * star_r * 1.6
        _star(draw, cx, cy, star_r, STAR_GRAY)

    wordmark_y = y + target_height * 0.32
    draw.text((x, wordmark_y), wordmark, font=wordmark_font, fill=WHITE)

    europe_text = "E U R O P E"
    europe_y = wordmark_y + target_height * 0.56
    europe_w = draw.textlength(europe_text, font=europe_font)
    draw.text((x + (wordmark_w - europe_w) / 2, europe_y), europe_text, font=europe_font, fill=YELLOW)

    return round(wordmark_w)


# ---------------------------------------------------------------- widgets

def _pill(draw, xy, text, font, fg, bg, pad_x=22, pad_y=12):
    x, y = xy
    w = draw.textlength(text, font=font)
    ascent, descent = font.getmetrics()
    h = ascent + descent
    box = (x, y, x + w + 2 * pad_x, y + h + 2 * pad_y)
    draw.rounded_rectangle(box, radius=(h + 2 * pad_y) / 2, fill=bg)
    draw.text((x + pad_x, y + pad_y), text, font=font, fill=fg)
    return box


def _circle_badge(img, center, radius, text, font):
    draw = ImageDraw.Draw(img)
    cx, cy = center
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=BLACK)
    max_width = radius * 1.5
    lines = _wrap(draw, text, font, max_width)[:3]
    ascent, descent = font.getmetrics()
    line_h = ascent + descent + 4
    total_h = line_h * len(lines)
    start_y = cy - total_h / 2
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        draw.text((cx - w / 2, start_y + i * line_h), line, font=font, fill=WHITE)


def _read_more_button(img, xy):
    draw = ImageDraw.Draw(img)
    font = _load_font(BOLD_FONT_PATH, 26)
    x, y = xy
    text = "Read More"
    w = draw.textlength(text, font=font)
    ascent, descent = font.getmetrics()
    h = ascent + descent
    pad_x, pad_y = 24, 14
    circle_d = h + 2 * pad_y - 8
    box = (x, y, x + w + 2 * pad_x + circle_d + 10, y + h + 2 * pad_y)
    draw.rounded_rectangle(box, radius=(h + 2 * pad_y) / 2, fill=PILL_BG)
    draw.text((x + pad_x, y + pad_y), text, font=font, fill=WHITE)
    circle_x = box[2] - circle_d - 8
    circle_y = y + (box[3] - y - circle_d) / 2
    draw.ellipse((circle_x, circle_y, circle_x + circle_d, circle_y + circle_d), fill=WHITE)
    ar_cx, ar_cy = circle_x + circle_d / 2, circle_y + circle_d / 2
    s = circle_d * 0.22
    draw.line((ar_cx - s, ar_cy, ar_cx + s, ar_cy), fill=BLACK, width=3)
    draw.line((ar_cx + s * 0.2, ar_cy - s * 0.7, ar_cx + s, ar_cy), fill=BLACK, width=3)
    draw.line((ar_cx + s * 0.2, ar_cy + s * 0.7, ar_cx + s, ar_cy), fill=BLACK, width=3)


# ---------------------------------------------------------------- headline

_HIGHLIGHT_RE = re.compile(r"\*\*(.+?)\*\*")
_AUTO_HIGHLIGHT_RE = re.compile(
    r"^[€$£]?[\d.,]+(?:[MBK]|million|billion|thousand)?%?$|^\d+%$", re.IGNORECASE
)


def _tokenize(text: str):
    """Splits headline text into (word, is_highlighted) tokens. Explicit
    **word** markup wins; otherwise money/percentage figures are
    auto-highlighted so numbers pop without manual tagging."""
    tokens = []
    for chunk in re.split(r"(\*\*.+?\*\*)", text):
        if not chunk:
            continue
        m = _HIGHLIGHT_RE.match(chunk)
        if m:
            for word in m.group(1).split():
                tokens.append((word, True))
        else:
            for word in chunk.split():
                tokens.append((word, bool(_AUTO_HIGHLIGHT_RE.match(word))))
    return tokens


def _wrap(draw, text, font, max_width):
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


def _wrap_tokens(draw, tokens, font, max_width):
    """Wraps highlight-tagged tokens into lines, preserving highlight state."""
    lines, current = [], []
    current_width = 0
    space_w = draw.textlength(" ", font=font)
    for word, hi in tokens:
        word_w = draw.textlength(word, font=font)
        extra = word_w if not current else space_w + word_w
        if current and current_width + extra > max_width:
            lines.append(current)
            current, current_width = [], 0
            extra = word_w
        current.append((word, hi))
        current_width += extra
    if current:
        lines.append(current)
    return lines


def _draw_headline(draw, tokens, xy, font, max_width, line_height):
    x0, y = xy
    lines = _wrap_tokens(draw, tokens, font, max_width)
    space_w = draw.textlength(" ", font=font)
    for line in lines:
        x = x0
        for word, hi in line:
            color = YELLOW if hi else WHITE
            draw.text((x, y), word, font=font, fill=color)
            x += draw.textlength(word, font=font) + space_w
        y += line_height
    return y


# ---------------------------------------------------------------- slide render

# top of the usable black area for headline text — must not creep into
# the light band above it, no matter how much text a slide is given
CONTENT_AREA_TOP = 660


def render_slide(
    headline_markup: str,
    output_path: Path,
    badge_text: str = None,
    company_text: str = None,
    read_more: bool = True,
    read_more_align: str = "right",
    font_size: int = 56,
) -> Path:
    """Renders a single slide. headline_markup supports **word** to force
    a yellow highlight; money/percentage figures are auto-highlighted.
    font_size controls the headline size — callers with longer body text
    should pass a smaller size (see render_carousel) so it still fits
    below CONTENT_AREA_TOP without overlapping the light band."""
    img = _background(with_light_band=True)
    draw = ImageDraw.Draw(img)
    margin = 64

    draw_logo(img, margin, 56, target_height=95)

    if company_text:
        _circle_badge(
            img,
            center=(CANVAS_SIZE[0] - margin - 90, 210),
            radius=90,
            text=company_text,
            font=_load_font(BOLD_FONT_PATH, 26),
        )

    if badge_text:
        _pill(
            draw,
            (margin, 400),
            badge_text,
            _load_font(REGULAR_FONT_PATH, 26),
            fg=WHITE,
            bg=PILL_BG,
        )

    headline_font = _load_font(BOLD_FONT_PATH, font_size)
    tokens = _tokenize(headline_markup)
    content_width = CANVAS_SIZE[0] - 2 * margin
    line_height = round(font_size * 1.18)
    lines = _wrap_tokens(draw, tokens, headline_font, content_width)
    text_block_height = line_height * len(lines)
    headline_y = max(CONTENT_AREA_TOP, CANVAS_SIZE[1] - 190 - text_block_height)
    end_y = _draw_headline(draw, tokens, (margin, headline_y), headline_font, content_width, line_height)

    divider_y = end_y + 18
    draw.line([(margin, divider_y), (CANVAS_SIZE[0] - margin, divider_y)], fill=(60, 62, 68), width=2)

    if read_more:
        button_y = divider_y + 24
        if read_more_align == "right":
            btn_w = 230
            _read_more_button(img, (CANVAS_SIZE[0] - margin - btn_w, button_y))
        else:
            _read_more_button(img, (margin, button_y))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path


# ---------------------------------------------------------------- carousel

def render_carousel(item: SourceItem, output_dir: Path = Path("output/review_queue")) -> list:
    """Renders a 2-3 slide carousel for a SourceItem:
      - slide 1: "Breaking News" badge, company circle badge (if item.company
        is set), and the headline
      - slide 2: supporting detail from item.summary
      - slide 3: only if summary is long enough to need a second detail slide
    Returns the list of rendered image paths, in post order.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c if c.isalnum() else "_" for c in item.item_id)[:60]
    base = f"{item.source.replace(':', '_')}__{safe_id}"

    paths = []

    slide1_path = output_dir / f"{base}__1.png"
    render_slide(
        item.headline,
        slide1_path,
        badge_text="Breaking News",
        company_text=item.company or None,
        read_more=True,
        read_more_align="right",
    )
    paths.append(slide1_path)

    summary = (item.summary or "").strip()
    if summary:
        detail_font_size = 42
        detail_line_height = round(detail_font_size * 1.18)
        max_lines_per_slide = max(1, (CANVAS_SIZE[1] - 190 - CONTENT_AREA_TOP) // detail_line_height)

        draw_probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        font = _load_font(BOLD_FONT_PATH, detail_font_size)
        content_width = CANVAS_SIZE[0] - 2 * 64

        # pack whole sentences into each slide (never split mid-sentence)
        # rather than wrapping blindly by line count
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", summary) if s.strip()]
        chunks, current = [], ""
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip()
            candidate_lines = len(_wrap(draw_probe, candidate, font, content_width))
            if current and candidate_lines > max_lines_per_slide:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            chunks.append(current)
        chunks = chunks[:2]  # cap total slides at 3 (headline + up to 2 detail slides)

        for i, chunk in enumerate(chunks):
            slide_path = output_dir / f"{base}__{i + 2}.png"
            render_slide(
                chunk,
                slide_path,
                badge_text=None,
                company_text=None,
                read_more=True,
                read_more_align="left",
                font_size=detail_font_size,
            )
            paths.append(slide_path)

    return paths


def render(item: SourceItem, output_dir: Path = Path("output/review_queue")) -> Path:
    """Back-compat single-image render (first slide only)."""
    return render_carousel(item, output_dir)[0]
