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

from signal_bot import downloader, graph_api, reposts, scheduler
from signal_bot.queue import QueueStore

logger = logging.getLogger(__name__)

# how often the tick loop wakes to check for due items
TICK_INTERVAL_SECONDS = 60


def _allowed_user_ids() -> set:
    raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def _build_caption(video: downloader.DownloadedVideo) -> str:
    parts = []
    if video.caption:
        parts.append(video.caption)
    credit = f"🎬 Original: @{video.owner_username}" if video.owner_username else "🎬 Reposted"
    parts.append(credit)
    parts.append("#EuropeanVC #AI #Startups #SignalEurope")
    return "\n\n".join(parts)


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
        "Send me an Instagram post or reel link and I'll queue it for the Signal Europe account.\n\n"
        f"Spacing: {lo}–{hi}h between posts\n"
        f"Quiet hours: {qs}:00–{qe}:00 ({tz})\n\n"
        "Commands: /queue, /cancel <id>, /next, /reposts"
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
    if not downloader.is_instagram_url(text):
        await update.message.reply_text(
            "That doesn't look like an Instagram post/reel link. Send a URL like "
            "https://www.instagram.com/reel/XXXXXXXXX/"
        )
        return

    store: QueueStore = context.application.bot_data["store"]

    # Dedup: refuse if we've already queued/posted this exact URL. A user
    # who genuinely wants to repost the same source URL again can wait
    # for the cooldown to lapse via the weekly picker, or use a
    # different source URL for the same video.
    existing = store.find_by_source(text)
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
        video = await asyncio.to_thread(downloader.download, text)
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
    finally:
        # local file no longer needed — the video host has it, and
        # reposts will reuse the same public URL
        video.local_path.unlink(missing_ok=True)

    caption = _build_caption(video)
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
    await status_msg.edit_text(
        f"Queued as #{item_id}, scheduled for {_fmt_local(scheduled_at)}. "
        "Use /queue to see everything pending, /cancel to remove."
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
