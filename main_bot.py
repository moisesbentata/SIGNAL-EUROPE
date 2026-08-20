"""Entry point: runs the Telegram bot that turns Instagram links into
posts on the Signal Europe account.

Usage:
    python main_bot.py

Runs forever (polling Telegram for messages) — needs to stay running
somewhere for the bot to respond. See README.md for hosting options.
"""

from signal_bot.bot import run

if __name__ == "__main__":
    run()
