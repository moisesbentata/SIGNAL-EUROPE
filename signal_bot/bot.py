"""Telegram bot: message it an Instagram post/reel link, it downloads
the video, uploads it to your video host once, and adds it to a queue
that publishes to the Signal Europe Instagram account with min
MIN_POST_GAP_HOURS between posts and no posts inside quiet hours.

Whitelisted to specific Telegram user IDs (TELEGRAM_ALLOWED_USER_IDS) —
without that, anyone who finds the bot's username could post to your
IG account.

Also runs a weekly job (Sunday 22:00 in POSTING_TIMEZONE) that picks
the top-performing posts of the last week by reach and adds them back
to the queue spread across the coming week. See signal_bot/reposts.py.

Commands:
  /start      — hello message with current settings
  /queue      — show all pending items with scheduled times
  /cancel <id> — remove a pending queue item
  /next       — jump the next pending item to now (still gated by
                quiet-hours and the min-gap-since-last-posted rules)
  /reposts    — trigger the weekly repost picker manually
"""

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, time, timezone

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from pathlib import Path

from signal_bot import downloader, graph_api, news_graphic, news_video, reposts, scheduler
from signal_bot.queue import QueueStore

NEWS_ASSETS_DIR = Path("data/news_assets")

logger = logging.getLogger(__name__)

# how often the tick loop wakes to check for due items
TICK_INTERVAL_SECONDS = 60


def _allowed_user_ids() -> set:
    raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def _build_caption(video: downloader.DownloadedVideo, custom_hook: str = "") -> str:
    """Signal Europe's own caption for a reposted reel.

    Deliberately ignores the original poster's caption — those routinely
    contain CTAs that make no sense on our account ("comment 80 for the
    link", "double-tap if you agree", etc). If the user provided
    additional text alongside the link, it becomes the hook. Credits go
    at the bottom, above the hashtags.
    """
    parts = []
    if custom_hook:
        parts.append(custom_hook.strip())

    credit_lines = []
    if video.owner_username:
        credit_lines.append(f"🎥 Original: @{video.owner_username}")
    if credit_lines:
        parts.append("\n".join(credit_lines))

    parts.append("#SignalEurope #EuropeanVC #AI #Startups #TechEurope")
    return "\n\n".join(parts)


# matches an IG post/reel URL embedded anywhere in a message (not just
# at the very start, so the user can include a custom hook alongside it)
_IG_URL_IN_TEXT_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:p|reel|reels)/[A-Za-z0-9_-]+/?[^\s]*",
    re.IGNORECASE,
)


def _parse_link_message(text: str) -> tuple:
    """Extracts (ig_url, custom_hook) from a message. custom_hook is
    everything else in the message stripped clean; empty if nothing."""
    m = _IG_URL_IN_TEXT_RE.search(text)
    if not m:
        return None, ""
    hook = (text[: m.start()] + " " + text[m.end():]).strip()
    return m.group(0), hook


async def _check_allowed(update: Update) -> bool:
    allowed = _allowed_user_ids()
    user_id = update.effective_user.id if update.effective_user else None
    if not allowed:
        await update.message.reply_text(
            "TELEGRAM_ALLOWED_USER_IDS isn't configured — refusing to run unrestricted. "
            "Set it in .env to your Telegram user ID."
        )
        return False
    if user_id not in allowed:
        await update.message.reply_text("Not authorized.")
        return False
    return True


def _fmt_local(dt: datetime) -> str:
    """Human-friendly timestamp in the configured posting timezone."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(os.getenv("POSTING_TIMEZONE", "Europe/Madrid"))
    return dt.astimezone(tz).strftime("%a %d %b, %H:%M")


# --------------------------------------------------------------- commands


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lo = os.getenv("MIN_POST_GAP_HOURS", "4")
    hi = os.getenv("MAX_POST_GAP_HOURS", "5")
    qs = os.getenv("QUIET_HOURS_START", "23")
    qe = os.getenv("QUIET_HOURS_END", "8")
    tz = os.getenv("POSTING_TIMEZONE", "Europe/Madrid")
    await update.message.reply_text(
        "Send me an Instagram post or reel link and I'll queue it for the "
        "Signal Europe account. Add your own hook line before or after the "
        "link and it becomes the caption — otherwise you get credit + hashtags "
        "only, and the original poster's caption is *not* copied over.\n\n"
        "Or send a NEWS message like:\n"
        "```\n"
        "NEWS\n"
        "Your hook headline here\n"
        "Longer description of the news.\n"
        "https://source-url.example (optional)\n"
        "```\n"
        "…and I'll generate a branded Reel graphic and queue it too.\n\n"
        "Every queued item comes back to you as a preview (the video + the "
        "exact caption) so you see what will publish before it goes live.\n\n"
        f"Spacing: {lo}–{hi}h between posts\n"
        f"Quiet hours: {qs}:00–{qe}:00 ({tz})\n\n"
        "Commands: /queue, /cancel <id>, /next, /reposts",
        parse_mode="Markdown",
    )


async def queue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_allowed(update):
        return
    store: QueueStore = context.application.bot_data["store"]
    items = store.pending()
    if not items:
        await update.message.reply_text("Queue is empty.")
        return

    lines = ["Pending queue:"]
    for item in items:
        tag = " 🔁" if item.is_repost else ""
        headline = (item.caption.split("\n")[0][:70] + "…") if item.caption else item.source_url
        lines.append(f"#{item.id}{tag}  {_fmt_local(item.scheduled_at)}  — {headline}")
    await update.message.reply_text("\n".join(lines))


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_allowed(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /cancel <id>  (see /queue for ids)")
        return
    try:
        item_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Id must be a number.")
        return
    store: QueueStore = context.application.bot_data["store"]
    if store.cancel(item_id):
        await update.message.reply_text(f"Cancelled #{item_id}.")
    else:
        await update.message.reply_text(
            f"Couldn't cancel #{item_id} — either not found or already posting/posted."
        )


async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_allowed(update):
        return
    store: QueueStore = context.application.bot_data["store"]
    pending = store.pending()
    if not pending:
        await update.message.reply_text("Queue is empty, nothing to bump.")
        return
    item = pending[0]
    store.reschedule(item.id, datetime.now(timezone.utc))
    await update.message.reply_text(
        f"Bumped #{item.id} to now — will publish on the next tick, still "
        "subject to quiet-hours and min-gap rules."
    )


async def reposts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_allowed(update):
        return
    store: QueueStore = context.application.bot_data["store"]
    await update.message.reply_text("Running weekly repost picker…")
    ids = await asyncio.to_thread(reposts.run_weekly_repost, store)
    if not ids:
        await update.message.reply_text(
            "No qualifying posts to repost (need posts ≥48h old, not "
            "reposted in the last 30 days, and with reach data available)."
        )
        return
    await update.message.reply_text(
        f"Queued {len(ids)} repost(s): {', '.join('#' + str(i) for i in ids)}. "
        "Check /queue for scheduled times."
    )


# --------------------------------------------------------------- link intake


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_allowed(update):
        return

    text = (update.message.text or "").strip()

    # NEWS trigger: message that starts with "NEWS" (case-insensitive)
    # goes through the branded-graphic pipeline instead of the video
    # downloader. We check this first so a NEWS message that happens to
    # contain an IG link isn't misrouted.
    if re.match(r"^\s*news\b", text, flags=re.IGNORECASE):
        await handle_news_message(update, context, text)
        return

    url, custom_hook = _parse_link_message(text)
    if not url:
        await update.message.reply_text(
            "That doesn't look like an Instagram post/reel link. Send a URL like "
            "https://www.instagram.com/reel/XXXXXXXXX/ — optionally with your own "
            "hook line before or after it. Or start with NEWS to build a branded "
            "news Reel from text."
        )
        return

    store: QueueStore = context.application.bot_data["store"]

    # Dedup: refuse if we've already queued/posted this exact URL. A user
    # who genuinely wants to repost the same source URL again can wait
    # for the cooldown to lapse via the weekly picker, or use a
    # different source URL for the same video.
    existing = store.find_by_source(url)
    if existing:
        if existing.status == "posted":
            when = _fmt_local(existing.posted_at) if existing.posted_at else "earlier"
            await update.message.reply_text(
                f"Already posted this link (as #{existing.id}, {when}). Skipping."
            )
        else:
            await update.message.reply_text(
                f"Already in the queue as #{existing.id}, scheduled for "
                f"{_fmt_local(existing.scheduled_at)}. Skipping."
            )
        return

    status_msg = await update.message.reply_text("Downloading…")

    try:
        video = await asyncio.to_thread(downloader.download, url)
    except downloader.DownloadError as exc:
        await status_msg.edit_text(f"Download failed: {exc}")
        return

    if video.exceeds_graph_api_limit:
        size_mb = video.size_bytes / (1024 * 1024)
        video.local_path.unlink(missing_ok=True)
        await status_msg.edit_text(
            f"Video is {size_mb:.0f}MB — over Instagram's ~100MB limit for "
            "Reels via API. Can't upload without re-encoding, which this "
            "bot deliberately doesn't do (would lose quality)."
        )
        return

    await status_msg.edit_text("Uploading to video host…")
    try:
        video_url = await asyncio.to_thread(graph_api.upload_video, str(video.local_path))
    except graph_api.GraphAPIError as exc:
        video.local_path.unlink(missing_ok=True)
        await status_msg.edit_text(f"Upload failed: {exc}")
        return

    caption = _build_caption(video, custom_hook=custom_hook)
    scheduled_at = scheduler.compute_enqueue_slot(
        store.last_scheduled(), datetime.now(timezone.utc)
    )
    item_id = store.enqueue(
        source_url=video.source_url,
        video_url=video_url,
        caption=caption,
        owner_username=video.owner_username,
        scheduled_at=scheduled_at,
    )

    # Send the video back as a preview so you see exactly what will
    # publish (both the actual video and the caption). Fall back to a
    # plain text reply if Telegram rejects the video for any reason.
    header = (
        f"Queued as #{item_id}, scheduled for {_fmt_local(scheduled_at)}.\n"
        f"— Caption preview below —"
    )
    try:
        with open(video.local_path, "rb") as f:
            await update.message.reply_video(video=f, caption=header)
        await update.message.reply_text(caption)
        await status_msg.delete()
    except Exception:  # noqa: BLE001
        await status_msg.edit_text(f"{header}\n\n{caption}")
    finally:
        # local file no longer needed — the video host has it, and
        # reposts will reuse the same public URL
        video.local_path.unlink(missing_ok=True)


# --------------------------------------------------------------- news graphic


def _build_news_caption(headline: str, description: str, source_url: str) -> str:
    parts = [headline]
    if description:
        parts.append(description)
    if source_url:
        parts.append(f"Source: {source_url}")
    parts.append("#SignalEurope #EuropeanVC #AI #Startups")
    return "\n\n".join(parts)


async def handle_news_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    headline, description, source_url = news_graphic.parse_news_message(text)
    if not headline:
        await update.message.reply_text(
            "Couldn't find a headline in that NEWS message. Format:\n"
            "```\nNEWS\n<Headline>\n<Description>\n<url>\n```",
            parse_mode="Markdown",
        )
        return

    store: QueueStore = context.application.bot_data["store"]

    # de-dup on the source URL if one is present; if the news post is
    # text-only, fall back to a hash-like handle so repeated identical
    # texts don't stack
    dedup_key = source_url or f"news:{hash(headline + description) & 0xffffffff:x}"
    existing = store.find_by_source(dedup_key)
    if existing and existing.status != "cancelled":
        if existing.status == "posted":
            when = _fmt_local(existing.posted_at) if existing.posted_at else "earlier"
            await update.message.reply_text(f"Already posted this news (as #{existing.id}, {when}). Skipping.")
        else:
            await update.message.reply_text(
                f"Already in the queue as #{existing.id}, scheduled for "
                f"{_fmt_local(existing.scheduled_at)}. Skipping."
            )
        return

    status_msg = await update.message.reply_text("Generating graphic…")

    NEWS_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    slug = uuid.uuid4().hex[:12]
    png_path = NEWS_ASSETS_DIR / f"news_{slug}.png"
    mp4_path = NEWS_ASSETS_DIR / f"news_{slug}.mp4"

    try:
        await asyncio.to_thread(news_graphic.render, headline, description, png_path)
    except Exception as exc:  # noqa: BLE001
        await status_msg.edit_text(f"Graphic generation failed: {exc}")
        return

    await status_msg.edit_text("Rendering to MP4…")
    try:
        await asyncio.to_thread(news_video.png_to_reel_mp4, png_path, mp4_path)
    except news_video.VideoBuildError as exc:
        png_path.unlink(missing_ok=True)
        await status_msg.edit_text(f"Video build failed: {exc}")
        return

    await status_msg.edit_text("Uploading to video host…")
    try:
        video_url = await asyncio.to_thread(graph_api.upload_video, str(mp4_path))
    except graph_api.GraphAPIError as exc:
        await status_msg.edit_text(f"Upload failed: {exc}")
        return
    finally:
        # keep the png locally as a preview (small, useful for debugging);
        # drop the mp4 since Cloudinary has it now
        mp4_path.unlink(missing_ok=True)

    caption = _build_news_caption(headline, description, source_url)
    scheduled_at = scheduler.compute_enqueue_slot(
        store.last_scheduled(), datetime.now(timezone.utc)
    )
    item_id = store.enqueue(
        source_url=dedup_key,
        video_url=video_url,
        caption=caption,
        owner_username="",
        scheduled_at=scheduled_at,
    )

    # send the preview PNG back to Telegram so you see what will publish
    try:
        with open(png_path, "rb") as f:
            await update.message.reply_photo(
                photo=f,
                caption=(
                    f"Queued as #{item_id}, scheduled for {_fmt_local(scheduled_at)}."
                ),
            )
        await status_msg.delete()
    except Exception:  # noqa: BLE001 - preview send is best-effort
        await status_msg.edit_text(
            f"Queued as #{item_id}, scheduled for {_fmt_local(scheduled_at)}."
        )


# --------------------------------------------------------------- publishing tick


async def _tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs every TICK_INTERVAL_SECONDS. Publishes at most one item per
    tick — that's enough since the min-gap rule prevents anything else
    from being due for hours anyway."""
    store: QueueStore = context.application.bot_data["store"]
    now = datetime.now(timezone.utc)

    item = store.next_due(now)
    if item is None:
        return

    allowed, reason = scheduler.can_publish_now(now, store.last_posted_at())
    if not allowed:
        logger.info("tick: holding off — %s", reason)
        return

    if not store.mark_posting(item.id):
        # something else already grabbed it (shouldn't happen with our
        # single-process model, but guards against it)
        return

    logger.info("tick: publishing queue item #%s", item.id)
    try:
        media_id = await asyncio.to_thread(graph_api.publish_reel, item.video_url, item.caption)
        store.mark_posted(item.id, media_id)
        logger.info("tick: published #%s -> media_id=%s", item.id, media_id)
        await _notify_owners(context, f"✅ Published #{item.id} (media {media_id}).")
    except graph_api.GraphAPIError as exc:
        store.mark_failed(item.id, str(exc))
        logger.exception("tick: publish failed for #%s", item.id)
        await _notify_owners(context, f"❌ Publish failed for #{item.id}: {exc}")


async def _weekly_repost_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    store: QueueStore = context.application.bot_data["store"]
    ids = await asyncio.to_thread(reposts.run_weekly_repost, store)
    if ids:
        await _notify_owners(
            context,
            f"🔁 Weekly repost picker queued {len(ids)} item(s): "
            + ", ".join("#" + str(i) for i in ids),
        )


async def _notify_owners(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Fires a message to every whitelisted user — so a fully async
    publish that happens while nobody's watching still surfaces."""
    for uid in _allowed_user_ids():
        try:
            await context.bot.send_message(chat_id=uid, text=text)
        except Exception:  # noqa: BLE001 - one failed DM shouldn't stop the rest
            logger.exception("failed to notify user %s", uid)


# --------------------------------------------------------------- app wiring


def build_app() -> Application:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

    app = Application.builder().token(token).build()
    app.bot_data["store"] = QueueStore()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("queue", queue_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("next", next_cmd))
    app.add_handler(CommandHandler("reposts", reposts_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if app.job_queue is None:
        raise RuntimeError(
            "python-telegram-bot's job_queue is unavailable — install with "
            "`pip install \"python-telegram-bot[job-queue]\"` (already in requirements.txt)."
        )

    app.job_queue.run_repeating(_tick, interval=TICK_INTERVAL_SECONDS, first=10)

    # Weekly repost job: Sunday 22:00 in the configured local timezone.
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(os.getenv("POSTING_TIMEZONE", "Europe/Madrid"))
    app.job_queue.run_daily(
        _weekly_repost_tick,
        time=time(hour=22, minute=0, tzinfo=tz),
        days=(6,),  # Sunday (Mon=0 ... Sun=6)
    )
    return app


def run() -> None:
    app = build_app()
    app.run_polling()
