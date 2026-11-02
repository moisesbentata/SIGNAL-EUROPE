# Signal Europe — Instagram repost bot

A Telegram bot that reposts to **your** Instagram account. You message it
an Instagram or Twitter/X link; it downloads the media, writes an
AI-generated caption (hook + engagement question), and publishes it to
your IG account through Instagram's official Graph API — pacing posts a
few hours apart so the account doesn't look spammy. It also re-queues your
best-performing posts each week.

Everything is configured with environment variables, so you can run your
**own** copy — your Telegram bot, your Instagram account — without
touching the code. This guide walks you through it start to finish; no
coding required.

> ⏱️ Realistic time to set up: **~30–45 min**, most of it the one-time
> Instagram/Meta developer setup. You do it once.

---

## What it can do

| You send… | It does… |
|---|---|
| An Instagram or X link | Downloads the media, writes a caption, and sends you a **preview to approve** — reposts in the **same shape** (reel / photo / carousel, slide order preserved) |
| A link **+ a note** ("make it about France", "shorter") | Writes the caption **your way** |
| `/copy <link>` | Mirrors the **original caption exactly** — no AI rewrite |
| `/format <link>` | Stamps your **watermark** on every item so it reads as your own content |
| A `NEWS` message + headline + description | Builds a branded 1080×1920 news Reel graphic |
| `/health` | Shows which credentials are set and what each missing one disables |

- **Preview & approve** — every submission comes back as a preview (media + exact caption) with ✅ Approve / 🚀 Publish now / ❌ Discard buttons. Nothing publishes until you tap one. (Turn the gate off with `REQUIRE_APPROVAL=false` to auto-queue.)
- **Paced queue** — approved posts chain onto each other with a random 4–5h gap, so a batch spreads out instead of burst-posting.
- **Weekly repost picker** — every Sunday it re-queues your top posts of the week (by Instagram reach) across the coming week.
- **AI captions** — hook line + a European-angle context sentence + a comment-farming question, generated from the media with Google Gemini (free tier). Falls back to credit + hashtags if you skip the Gemini key.
- **Preview before it posts** — every queued item comes back to you in Telegram so you see exactly what will publish.
- **Access control** — only the Telegram user IDs you list can make it post.

## Make it *your* format

Account name, brand colour, hashtags, caption voice, and the whole
**NEWS graphic design** are controlled by one optional file,
`assets/branding.json` — no code changes. You can even design the news
card in **Figma or Canva**, export a 1080×1920 background, and have the
bot render headlines onto your template. Full walkthrough in
**[BRANDING.md](BRANDING.md)**. With no branding file, you get the
default Signal Europe look.

---

## The 4 things you need to connect

You'll collect a handful of values and paste them in as environment
variables. Here's the map — details for each below.

| What | Required? | Unlocks | Free? |
|---|---|---|---|
| **Telegram bot** | ✅ yes | The bot itself | Free |
| **Instagram Graph API** | ✅ yes | Publishing to your IG | Free |
| **Cloudinary** | ✅ yes | Media hosting (IG fetches posts from a public URL) | Free tier |
| **Google Gemini** | optional | AI captions (hook + question) | Free tier |

Two more optional ones (`IG_COOKIES_B64`, `SCRAPER_PROXY`) help with
downloading source posts reliably from the cloud — see
[Downloading reliably](#downloading-reliably-optional-but-recommended).

---

## Step 1 — Telegram bot (5 min)

1. Open Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, and follow the prompts (pick a name and a username ending in `bot`).
2. BotFather gives you a **token** like `12345678:AA...`. That's your `TELEGRAM_BOT_TOKEN`.
3. Message [@userinfobot](https://t.me/userinfobot); it replies with your numeric **user ID**. That's your `TELEGRAM_ALLOWED_USER_IDS` (comma-separate if more than one person should be allowed to post).

## Step 2 — Instagram Graph API (20–30 min, one-time)

This is the fiddly part, but you only do it once.

1. **Make the IG account a Business or Creator account**: Instagram app → Settings → *Account type and tools* → Switch to professional account.
2. **Create a Facebook Page** and link your IG account to it (IG app → Settings → *Accounts Center* / *Linked accounts*). The Page can be empty; it just has to exist and be linked.
3. Go to [developers.facebook.com](https://developers.facebook.com) → **My Apps** → *Create App* → type **Business**. Add the **Instagram** product (Instagram Graph API).
4. Using the Graph API Explorer (or the app dashboard), generate an access token with these permissions: `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `instagram_manage_insights` (the last one is what lets the weekly repost picker read reach — without it it does nothing).
5. **Convert it to a long-lived token** so it lasts ~60 days instead of 1 hour, and ideally a permanent Page token. (The bot's `graph_api.py` uses whatever you give it; see the token exchange docs linked below.)
6. Find your **IG Business Account ID**: `GET /{your-page-id}?fields=instagram_business_account&access_token=…` in the Graph API Explorer.

Put the token in `IG_ACCESS_TOKEN` and the id in `IG_BUSINESS_ACCOUNT_ID`.

> You do **not** need to submit the app for review — your own account uses it in Development Mode. Review is only needed if *other people's* accounts will use your app.
>
> Token exchange reference: [Long-lived tokens](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived) · [Content Publishing API](https://developers.facebook.com/docs/instagram-platform/content-publishing).

## Step 3 — Cloudinary (3 min)

Instagram's API fetches each post from a public URL, so media is uploaded
to Cloudinary first.

1. Sign up free at [cloudinary.com](https://cloudinary.com).
2. Dashboard → **API Environment variable** — copy the whole value, which looks like `cloudinary://123456789:abcdef@your-cloud-name`.
3. Paste it verbatim into `CLOUDINARY_URL`.

## Step 4 — Google Gemini (optional, 2 min)

For AI captions. Skip it and you get credit + hashtags only.

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) → *Create API key* (no card needed).
2. Paste it into `GEMINI_API_KEY`.

---

## Deploying on Railway (recommended)

The bot has to stay running to receive messages and drain the queue, so
run it on a host, not your laptop. [Railway](https://railway.app) is the
easiest.

1. **Get the code**: fork this repo on GitHub (or push your own copy).
2. In [railway.app](https://railway.app): *New Project* → *Deploy from GitHub repo* → pick the repo. Railway reads `railway.json` and starts it with `python main_bot.py`.
3. Open the service → **Variables** → add each value you collected above. At minimum:
   ```
   TELEGRAM_BOT_TOKEN
   TELEGRAM_ALLOWED_USER_IDS
   IG_BUSINESS_ACCOUNT_ID
   IG_ACCESS_TOKEN
   CLOUDINARY_URL
   GEMINI_API_KEY          (optional)
   DATA_DIR=/data
   ```
4. **Add a volume** (optional but recommended): service → *Variables/Settings* → *Volumes* → mount at `/data`, and set `DATA_DIR=/data`. Without it the queue resets on every redeploy (posts already published stay published; only *pending* ones are lost).
5. Watch the deploy logs — the bot prints a **configuration checklist** on startup showing exactly what's set and what's missing.
6. Open Telegram, message your bot `/health` to confirm everything's green, then send it a link.

> `.env.example` lists **every** variable with inline explanations — open it for the full reference, including the optional cookies/proxy settings.

### Running locally instead

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # then fill it in
python main_bot.py
```

---

## Downloading reliably (optional but recommended)

Instagram and Twitter block requests from datacenter IPs (which is what
Railway, AWS, etc. have). So downloads that work on your laptop can fail
in the cloud with "empty media response" / "login required". Two things
help:

- **`IG_COOKIES_B64` / `TWITTER_COOKIES_B64`** — export `cookies.txt` from a browser logged into a **throwaway** IG/X account (not your posting account), base64-encode the file (`base64 -w0 cookies.txt`), and paste the result. This logs the downloader in.
- **`SCRAPER_PROXY`** — a residential proxy URL (`http://user:pass@host:port`). This is the most reliable fix for cloud blocking; providers run ~$1–3/GB and downloads are tiny. See `.env.example` for details.

Run `/health` any time to see which of these you've set.

---

## Commands

- `/health` — configuration checklist: what's set, what's missing, what each unlocks.
- `/start` — hello + current spacing/approval settings.
- `/queue` — list pending items with scheduled times.
- `/cancel <id>` — remove a pending item.
- `/next` — bump the next item to now (still respects the min-gap-since-last-post rule).
- `/reposts` — run the weekly repost picker now.
- `/copy <link>` — repost with the original caption copied exactly.
- `/format <link>` — repost stamped with your watermark.
- Send a link **+ a note** to steer the caption; send `NEWS` + headline + description to build a branded news Reel.

---

## The copyright point, stated plainly

This reposts other people's actual media to your account. The auto-added
`🎥 Original: @creator` credit line is courtesy, **not** permission — credit
isn't a license. You are the one vetting each link before you send it; the
bot can't know whether a given repost is okay. `/format` additionally
brands the media as yours, which makes that your call to get right.

## Good to know

- **Access tokens expire** (~60 days unless you made a permanent Page token). Regenerate `IG_ACCESS_TOKEN` before then or the bot goes quiet.
- **yt-dlp** occasionally breaks when Instagram changes their site; `pip install -U yt-dlp` (it's pinned in `requirements.txt`, bump it) usually fixes downloads.
- **Single process** — don't run two copies against the same queue DB.
- **One account per deploy** — each copy posts to exactly one Instagram account. Your friend runs their own copy with their own variables; nothing is shared between them.
