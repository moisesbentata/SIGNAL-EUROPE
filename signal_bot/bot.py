"""Telegram bot: message it an Instagram post/reel link, it downloads the
video and (after you confirm) posts it to the Signal Europe Instagram
account via the Graph API.

Whitelisted to specific Telegram user IDs (TELEGRAM_ALLOWED_USER_IDS) —
without that, anyone who finds the bot's username could post to your IG
account. Posts immediately on link receipt by default (the assumption
being you vet copyright/fit before ever sending a link) — set
REQUIRE_CONFIRMATION=true in .env if you want an inline Yes/No check
before it actually publishes.
"""

import asyncio
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from signal_bot import downloader, graph_api

# in-memory pending-post store: short token -> DownloadedVideo
# fine for a single-instance bot; if you ever run multiple workers this
# needs to move to shared storage (e.g. SQLite, redis)
_PENDING: dict = {}


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send me an Instagram post or reel link and I'll download it and "
        "post it to the Signal Europe account (with your confirmation first)."
    )


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

    status_msg = await update.message.reply_text("Downloading...")

    try:
        video = await asyncio.to_thread(downloader.download, text)
    except downloader.DownloadError as exc:
        await status_msg.edit_text(f"Download failed: {exc}")
        return

    require_confirmation = os.getenv("REQUIRE_CONFIRMATION", "false").strip().lower() == "true"
    caption = _build_caption(video)

    if not require_confirmation:
        await status_msg.edit_text("Publishing...")
        await _publish_and_report(video, caption, status_msg)
        return

    token = uuid.uuid4().hex[:16]
    _PENDING[token] = (video, caption)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Post it", callback_data=f"post:{token}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{token}"),
            ]
        ]
    )
    await status_msg.delete()
    with open(video.local_path, "rb") as f:
        await update.message.reply_video(
            video=f,
            caption=f"{caption}\n\n— Post this to Signal Europe?",
            reply_markup=keyboard,
        )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    if user_id not in _allowed_user_ids():
        await query.answer("Not authorized.", show_alert=True)
        return

    action, token = query.data.split(":", 1)
    pending = _PENDING.pop(token, None)
    if pending is None:
        await query.answer("This request expired or was already handled.", show_alert=True)
        return

    video, caption = pending

    if action == "cancel":
        await query.answer("Cancelled.")
        await query.edit_message_caption(caption=f"{caption}\n\n❌ Cancelled, not posted.", reply_markup=None)
        video.local_path.unlink(missing_ok=True)
        return

    await query.answer("Publishing...")
    await query.edit_message_caption(caption=f"{caption}\n\n⏳ Publishing...", reply_markup=None)
    await _publish_and_report(video, caption, query.message, is_edit=True)


async def _publish_and_report(video, caption, message, is_edit: bool = False) -> None:
    try:
        video_url = await asyncio.to_thread(graph_api.upload_video, str(video.local_path))
        media_id = await asyncio.to_thread(graph_api.publish_reel, video_url, caption)
        result_text = f"{caption}\n\n✅ Published (media id {media_id})."
    except graph_api.GraphAPIError as exc:
        result_text = f"{caption}\n\n❌ Publish failed: {exc}"
    finally:
        video.local_path.unlink(missing_ok=True)

    if is_edit:
        await message.edit_caption(caption=result_text)
    else:
        await message.edit_text(result_text)


def build_app() -> Application:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    return app


def run() -> None:
    app = build_app()
    app.run_polling()
