# Signal Europe — repost bot

A Telegram bot for the `Signal Europe` Instagram account: send it an
Instagram post/reel link and it downloads the video, queues it, and
publishes it to your IG account via the official Graph API — pacing
posts out with configurable spacing and quiet hours so the account
doesn't get flagged for burst-posting. It also picks your best-
performing posts every week and re-queues them.

## How it works

```
You (Telegram) --link--> bot --downloads (yt-dlp)--> uploads once to your
video host --SQLite queue--> tick loop publishes when a slot's available
                                                            │
                                          Sunday 22:00 ─────┴── weekly repost picker
                                                                (top N by IG reach)
```

- **Queue-based posting**: every link goes into a persistent queue with a scheduled time. New items chain onto the last-scheduled item plus a random gap in `[MIN_POST_GAP_HOURS, MAX_POST_GAP_HOURS]` (default 4-5h), then get snapped past **quiet hours** (default 23:00-08:00 Europe/Madrid) if they'd land inside. Send 3 links in 5 minutes → they publish across ~12h instead of all at once.
- **Weekly repost picker**: every Sunday 22:00 (local time), pulls Instagram reach numbers for posts from the past 7 days, picks the top N (default 3), and re-queues them spread across the coming week. Skips anything already reposted in the last 30 days (cooldown), and only considers posts ≥48h old (fresh posts haven't collected representative numbers). Reposts get a small caption prefix so Instagram doesn't flag them as identical duplicate content.
- **Duplicate refusal**: send the same IG link twice → the second time the bot tells you when the first was queued/posted and refuses.
- **Download**: `signal_bot/downloader.py` uses `yt-dlp` at highest available quality (no re-encoding). Works for public posts only. Videos over Instagram's ~100MB Reels API limit fail with a clear message rather than being silently downgraded.
- **Publish**: `signal_bot/graph_api.py` uses Meta's official Graph API. Each video is uploaded to your video host once — the returned public URL is stored in the queue DB and reused if the post is later re-queued, so reposting doesn't hit the download/upload path again.
- **Access control**: only Telegram user IDs listed in `TELEGRAM_ALLOWED_USER_IDS` can trigger anything. Without it the bot refuses to run.

## Commands

- `/start` — hello message, shows current spacing/quiet-hours config.
- `/queue` — list every pending item with scheduled times and captions.
- `/cancel <id>` — remove a pending item.
- `/next` — bump the next item to now (still respects quiet-hours + min-gap-since-last-posted).
- `/reposts` — trigger the weekly repost picker manually.

## Setup

### 1. Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create the Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram, `/newbot`, follow the prompts.
2. Copy the token into `TELEGRAM_BOT_TOKEN`.
3. Message [@userinfobot](https://t.me/userinfobot) for your numeric Telegram user ID, put it in `TELEGRAM_ALLOWED_USER_IDS` (comma-separate if more than one person should be able to trigger posts).

### 3. Instagram Graph API

1. Convert the IG account to **Business** or **Creator** (Instagram app → Settings → Account type).
2. Create a **Facebook Page** and link the IG account to it.
3. [developers.facebook.com](https://developers.facebook.com) → create an App → add the **Instagram Graph API** product.
4. Generate a long-lived access token (60 days, renewable) with `instagram_content_publish`, `pages_show_list`, `instagram_basic`, and `instagram_manage_insights` (this last one is what makes the weekly repost picker work — without it reach queries return empty).
5. Find `IG_BUSINESS_ACCOUNT_ID` via `GET /{page-id}?fields=instagram_business_account&access_token=…`.

You don't need to submit the app for review — your own account uses it in Development Mode. App review is only required if you want *other* accounts to use it.

### 4. Video hosting

The Graph API fetches video from a URL, not a file upload. Point `VIDEO_HOST_UPLOAD_URL` / `VIDEO_HOST_PUBLIC_BASE_URL` at any host you control (S3 with public read is cheapest). `signal_bot/graph_api.py::upload_video()` is a thin wrapper — swap its body for whatever your host's upload API expects. Videos are uploaded once and the URL is reused forever, so reposts don't re-upload.

### 5. Configure `.env`

```bash
cp .env.example .env
```

Fill in the credentials above. The rest have sensible defaults — spacing, quiet hours, repost cadence — but skim them.

### 6. Run it locally

```bash
python main_bot.py
```

Message the bot on Telegram with `/start`, then send an IG link.

## Deploying on Railway

The bot needs to stay running to catch messages and drain the queue on schedule.

1. Push this repo to GitHub, connect it in [railway.app](https://railway.app) → New Project → Deploy from GitHub.
2. In the service Settings → **Variables**, paste every non-blank value from your `.env`. Include `DATA_DIR=/data`.
3. Settings → **Volumes** → New Volume, mount at `/data`. **This is essential** — Railway wipes the container filesystem on every redeploy, so without a persistent volume the queue and posting history vanish (and scheduled reposts along with them). The free hobby tier includes 5GB, way more than this bot ever needs.
4. Railway auto-deploys from `railway.json` — start command is `python main_bot.py`.

Every push to the branch redeploys automatically. The volume persists across deploys, so the queue survives.

## What the bot deliberately doesn't do

- **No auto-strip of TikTok watermarks** — Instagram demotes watermarked reposts. Check the video before sending the link.
- **No re-encoding to fit under IG's size limit** — that would lose quality. Videos over ~100MB fail with a clear message.
- **No confirmation prompts** by default — the assumption is you vet each link before sending it. Flip `REQUIRE_CONFIRMATION=true` in `.env` to add an inline Yes/No step (not currently wired to the new queue flow — filed for later if you actually want it).
- **No cross-poster** — this posts to one IG account. Not Facebook, not TikTok.

## The copyright point, stated plainly

This reposts other people's actual videos to your account. Even with a `🎬 Original: @creator` credit line auto-added to every caption, credit isn't permission. You're the one vetting each link before sending it — the bot has no way to know whether a given repost is actually okay.

## Known limitations

- **yt-dlp Instagram support** breaks when IG changes their site. If downloads start failing, `pip install -U yt-dlp` first.
- **Access tokens expire** every 60 days. Regenerate before then or the bot goes silent.
- **Insights availability**: `instagram_manage_insights` permission is required for the weekly repost picker to work. Without it the picker returns zero and no reposts get queued.
- **Single-process queue**: the SQLite queue assumes one bot process. Don't run multiple copies against the same DB or two ticks could grab the same item.
