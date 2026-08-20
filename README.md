# Signal Europe — repost bot

A Telegram bot for the `Signal Europe` Instagram account: send it an
Instagram post/reel link, it downloads the video and posts it straight
to the account via the official Graph API.

## How it works

```
You (Telegram) --link--> bot --downloads video (yt-dlp)--> uploads to your
video host --publishes--> Instagram (Graph API, Reels)
```

- **Trigger**: message the bot an `instagram.com/p/...` or `instagram.com/reel/...` link on Telegram.
- **Download**: `signal_bot/downloader.py` uses `yt-dlp` against Instagram's public web interface. This is unofficial and against Instagram's Terms of Service — it works for public posts, can break or get rate-limited without warning, and won't work on private accounts.
- **Publish**: `signal_bot/graph_api.py` uses Meta's official Instagram Graph API to publish the downloaded video as a Reel — the only sanctioned way to post programmatically to your own account.
- **Access control**: only Telegram user IDs listed in `TELEGRAM_ALLOWED_USER_IDS` can trigger a post. This isn't optional — without it, anyone who finds the bot's username could post to your Instagram account.
- **Confirmation**: off by default (`REQUIRE_CONFIRMATION=false`) — the bot posts as soon as you send a link, on the assumption you're the one deciding what's worth reposting before you ever send it. Set it to `true` in `.env` if you'd rather get an inline Yes/No check first.

## The copyright point, stated plainly

This reposts someone else's actual video to your account. That's different from (and riskier than) building original graphics from extracted facts — it's a real infringement risk if the original creator hasn't agreed to it. The bot adds an automatic credit line (`🎬 Original: @username`) to every caption, but credit isn't the same as permission. You're the one vetting each link before sending it, so this is on you at send-time — the bot has no way to know whether reposting a given video is actually okay.

## Setup

### 1. Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create the Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, follow the prompts.
2. Copy the token it gives you into `TELEGRAM_BOT_TOKEN` in `.env`.
3. Message [@userinfobot](https://t.me/userinfobot) to get your own numeric Telegram user ID, put it in `TELEGRAM_ALLOWED_USER_IDS` (comma-separate if more than one person should be able to trigger posts).

### 3. Configure `.env`

```bash
cp .env.example .env
```

#### Instagram Graph API (required for publishing)

Same setup as any Graph API integration:

1. Convert the Signal Europe IG account to a **Business** or **Creator** account.
2. Create a **Facebook Page** and link the IG account to it.
3. Go to [developers.facebook.com](https://developers.facebook.com), create an App → add the **Instagram Graph API** product.
4. Generate a long-lived access token with `instagram_content_publish`, `pages_show_list`, and `instagram_basic` permissions.
5. Find `IG_BUSINESS_ACCOUNT_ID` via `GET /{page-id}?fields=instagram_business_account&access_token=...`.
6. Put both in `.env`.

Access tokens from the Graph API Explorer expire in ~1 hour — generate a **long-lived token** (60 days, renewable) for a bot that needs to keep working.

#### Video hosting (required for publishing)

The Graph API fetches the video from a URL you give it — it won't accept a local file. Point `VIDEO_HOST_UPLOAD_URL` / `VIDEO_HOST_PUBLIC_BASE_URL` at any host you control (S3, Cloudinary, your own static file server). `signal_bot/graph_api.py::upload_video()` is a thin wrapper — swap its body for whatever your host's upload API expects.

### 4. Run it

```bash
python main_bot.py
```

This runs forever, polling Telegram for messages. Message the bot a link and watch the terminal / Telegram chat for progress.

### 5. Keep it running (hosting)

The bot needs to stay running to respond to messages at any time — running it on your laptop only works while your laptop is on and the script is open. Options, roughly cheapest/simplest to most robust:

- **A small always-on VM** (a $5/mo DigitalOcean droplet, a free-tier AWS/GCP instance, etc.) — run `python main_bot.py` inside a `systemd` service or `tmux`/`screen` session so it survives disconnects.
- **A PaaS with a "worker" process type** (Railway, Render, Fly.io) — push this repo, set the environment variables in their dashboard, point it at `python main_bot.py` as a background worker (not a web service — this bot doesn't listen on a port).
- **A Raspberry Pi or spare machine** at home, if you want zero hosting cost and don't mind it going down when your internet does.

Whichever you pick, the `.env` values (bot token, IG credentials, video host) need to be set as environment variables or an `.env` file on that machine — don't commit `.env` itself (already gitignored).

## Known limitations

- yt-dlp's Instagram support can break when Instagram changes its site — if downloads start failing, check for a yt-dlp update (`pip install -U yt-dlp`) first.
- Age-restricted, private, or login-required posts won't download without adding cookie-based auth to `downloader.py` — not implemented here.
- The in-memory pending-post store (used when `REQUIRE_CONFIRMATION=true`) is lost if the bot restarts before you respond to a confirmation prompt — the downloaded file is orphaned in `downloads/` in that case and needs manual cleanup.
- Graph API access tokens expire; refresh periodically unless you build a refresh-token flow.
