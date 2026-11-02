"""Central configuration check.

A fresh deploy by someone who isn't the author should fail *loudly and
legibly* — telling them exactly which credential is missing and what it
unlocks — instead of starting up fine and then throwing a cryptic Graph
API / Cloudinary error only when they try to post.

`preflight()` logs a checklist at startup; the /health Telegram command
shows the same checklist on demand so the operator can verify their
setup from their phone without reading logs.
"""

import os
from dataclasses import dataclass


@dataclass
class Setting:
    key: str
    required: bool  # True = the bot can't do its core job (post to IG) without it
    unlocks: str    # plain-language description of what this enables
    where: str      # short hint on where to get it


# Order matters — this is the order the checklist is shown in, roughly
# "most essential first".
SETTINGS = [
    Setting(
        "TELEGRAM_BOT_TOKEN", True,
        "the bot itself — receiving your Telegram messages",
        "@BotFather on Telegram → /newbot",
    ),
    Setting(
        "TELEGRAM_ALLOWED_USER_IDS", True,
        "access control — the numeric IDs allowed to post (keeps strangers out)",
        "@userinfobot on Telegram",
    ),
    Setting(
        "IG_BUSINESS_ACCOUNT_ID", True,
        "publishing to your Instagram account",
        "Meta Graph API (see SETUP)",
    ),
    Setting(
        "IG_ACCESS_TOKEN", True,
        "publishing to your Instagram account",
        "Meta Graph API (see SETUP)",
    ),
    Setting(
        "CLOUDINARY_URL", True,
        "media hosting — Instagram fetches each post from a public URL",
        "cloudinary.com dashboard → API Environment variable",
    ),
    Setting(
        "GEMINI_API_KEY", False,
        "AI captions (hook + engagement question). Without it: credit + hashtags only",
        "aistudio.google.com/apikey (free)",
    ),
    Setting(
        "IG_COOKIES_B64", False,
        "reliably downloading source Instagram posts",
        "export cookies.txt from a throwaway IG login, base64 it",
    ),
    Setting(
        "SCRAPER_PROXY", False,
        "downloads from cloud IPs that Instagram blocks (Railway, AWS…)",
        "a residential proxy provider (~$1-3/GB)",
    ),
    Setting(
        "TWITTER_COOKIES_B64", False,
        "downloading some Twitter/X videos that need a login",
        "export cookies.txt from a throwaway X login, base64 it",
    ),
]


def is_set(key: str) -> bool:
    return bool((os.getenv(key) or "").strip())


def missing_required() -> list:
    """Required settings that aren't present. The bot can start without
    TELEGRAM_BOT_TOKEN-less items but can't actually post."""
    return [s for s in SETTINGS if s.required and not is_set(s.key)]


def status_lines() -> list:
    """Human-readable checklist, one line per setting."""
    lines = []
    for s in SETTINGS:
        ok = is_set(s.key)
        mark = "✅" if ok else ("❌" if s.required else "➖")
        tag = "" if ok else (" (REQUIRED — not set)" if s.required else " (optional — not set)")
        lines.append(f"{mark} {s.key}{tag}\n   → {s.unlocks}")
    return lines


def preflight(logger) -> list:
    """Logs the config checklist at startup. Returns the list of missing
    *required* settings so the caller can decide whether to hard-fail."""
    logger.info("=" * 60)
    logger.info("Signal Europe bot — configuration check")
    logger.info("=" * 60)
    for s in SETTINGS:
        ok = is_set(s.key)
        mark = "OK " if ok else ("MISSING" if s.required else "skip")
        logger.info("[%s] %s — %s", mark, s.key, s.unlocks)
    missing = missing_required()
    if missing:
        logger.warning(
            "%d required setting(s) missing: %s — the bot will run but "
            "can't post until these are set. Send /health to the bot to "
            "see this list on your phone.",
            len(missing), ", ".join(s.key for s in missing),
        )
    else:
        logger.info("All required settings present. Ready to post.")
    logger.info("=" * 60)
    return missing
