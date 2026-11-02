"""Per-operator branding.

Someone running their own copy of this bot should be able to make every
post look like *their* account — their name, colours, hashtags, caption
voice, and a news-graphic template they design themselves in Figma or
Canva — without touching any Python.

All of that lives in one file: `assets/branding.json`. Copy
`assets/branding.example.json` to start. Everything in it is optional —
anything you leave out falls back to the built-in default here, so an
empty `{}` reproduces the original Signal Europe look. Commit your own
branding (and any template/logo it points at) to your fork so a
deploy-from-GitHub host like Railway picks it up. See BRANDING.md.

Load order: `assets/branding.json` (or $BRANDING_FILE) merged *over*
DEFAULTS, deeply, so you only specify the keys you want to change.
"""

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

BRANDING_PATH = Path(os.getenv("BRANDING_FILE", "assets/branding.json"))

# The built-in defaults = the original Signal Europe identity. Every key
# an operator can override is present here, which doubles as the schema.
DEFAULTS = {
    # Shown as the watermark wordmark and the news-graphic brand mark,
    # and used by the caption writer to name the account.
    "brand_name": "SIGNAL EUROPE",
    # Primary accent colour (hex). Watermark wordmark, badge, brand mark.
    "accent_hex": "#FCEE0A",
    # Hashtags appended to every reposted caption.
    "hashtags": "#SignalEurope #EuropeanVC #AI #Startups #TechEurope",
    # --- caption voice (fed to the Gemini caption writer) ---
    "caption": {
        # One phrase describing what the account covers.
        "niche": "European venture capital, AI, and startup news",
        # The geographic/thematic angle the hook should lean into. Set to
        # "" to drop the angle requirement entirely.
        "angle": "European",
    },
    # --- news-graphic template (the NEWS command) ---
    "news": {
        # Your own 1080x1920 background, exported from Figma/Canva and
        # dropped in assets/. If the file exists it's used as-is and the
        # built-in navy gradient is skipped. Bake your logo, colours and
        # any fixed decoration into it; leave open space where the
        # headline and description text below will be drawn.
        "background_image": "assets/template/news_bg.png",
        "text_color": "#FFFFFF",
        "muted_color": "#A0A5B4",
        # The "BREAKING" pill. Disable it (or set your own text) if your
        # background already includes a label.
        "badge": {"enabled": True, "text": "BREAKING", "x": 84, "y": 84},
        # Headline text box. The font auto-shrinks to fit max_lines.
        "headline": {
            "x": 84, "y": 376, "width": 912, "max_lines": 4,
            "size_max": 110, "size_min": 58,
        },
        # Description paragraph, drawn below the headline.
        "description": {"x": 84, "width": 912, "size": 44, "line_height": 60, "gap": 60},
        # The bottom "SIGNAL EUROPE" brand mark. Turn off if your
        # background already carries your logo/name.
        "show_bottom_brand": True,
        # Draw the programmatic SGNL/EUROPE logo top-right. Turn off if
        # your background or assets/logo/logo.png already brands it.
        "show_top_logo": True,
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def load() -> dict:
    """Returns the merged branding config. Cached — a deployed bot picks
    up changes on restart (Railway redeploys restart the process)."""
    if BRANDING_PATH.exists():
        try:
            user = json.loads(BRANDING_PATH.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                logger.info("branding: loaded overrides from %s", BRANDING_PATH)
                return _deep_merge(DEFAULTS, user)
            logger.warning("branding: %s is not a JSON object, ignoring", BRANDING_PATH)
        except (ValueError, OSError) as exc:
            logger.warning("branding: couldn't read %s (%s), using defaults", BRANDING_PATH, exc)
    return dict(DEFAULTS)


def hex_to_rgb(value: str, default=(0, 0, 0)) -> tuple:
    try:
        h = value.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except (ValueError, TypeError, IndexError):
        return default


# --- convenience accessors ---------------------------------------------

def brand_name() -> str:
    return load().get("brand_name") or DEFAULTS["brand_name"]


def accent_rgb() -> tuple:
    return hex_to_rgb(load().get("accent_hex", ""), hex_to_rgb(DEFAULTS["accent_hex"]))


def hashtags() -> str:
    return load().get("hashtags", DEFAULTS["hashtags"])


def caption_niche() -> str:
    return (load().get("caption") or {}).get("niche", DEFAULTS["caption"]["niche"])


def caption_angle() -> str:
    return (load().get("caption") or {}).get("angle", DEFAULTS["caption"]["angle"])


def news() -> dict:
    return load().get("news", DEFAULTS["news"])
