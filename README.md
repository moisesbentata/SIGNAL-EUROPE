# Signal Europe — Instagram content pipeline

Automates content for the `Signal Europe` Instagram account (European VC &
AI). It watches news sources, turns new items into branded graphics, and
either queues them for you to review or auto-publishes them.

## How it works

```
RSS feeds (reliable)  ─┐
                        ├─> dedup (SQLite) -> branded image (Pillow) -> review_queue/ or auto-publish (Graph API)
IG accounts (best-effort) ┘
```

- **RSS feeds** (`config/feeds.yaml`) — EU-Startups, Sifted, Tech.eu, TechCrunch Europe. Official, reliable, no ToS risk.
- **Watched IG accounts** (`config/watched_accounts.yaml`) — currently `scaling.europe` and `vesting`. Uses `instaloader` against Instagram's public web interface. **This is unofficial and against Instagram's Terms of Service.** It can be rate-limited or blocked at any time without warning. Only caption *text* is read — the source accounts' images/videos are never copied or reposted.
- **Image generation** — every post is a fresh graphic rendered from extracted text (headline + summary) onto Signal Europe's own template (`signal_europe/content/image_gen.py`). Nothing from a source's actual image is ever reused, which keeps this on the right side of copyright.
- **Publishing** — via Meta's official Instagram Graph API (`signal_europe/publish/graph_api.py`), the only sanctioned way to post programmatically.

By default the pipeline does **not** auto-publish — it writes each candidate image + caption to `output/review_queue/` so you can check before anything goes live. Flip `AUTO_PUBLISH=true` in `.env` once you trust it.

## Setup

### 1. Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure `.env`

```bash
cp .env.example .env
```

#### Instagram Graph API (required for publishing)

The Graph API only lets you post to accounts *you* manage — this is the correct, sanctioned path for your own Signal Europe account:

1. Convert the Signal Europe IG account to a **Business** or **Creator** account (Instagram app → Settings → Account type).
2. Create a **Facebook Page** and link the IG account to it (Page Settings → Linked Accounts).
3. Go to [developers.facebook.com](https://developers.facebook.com), create an App → add the **Instagram Graph API** product.
4. Generate a long-lived access token with `instagram_content_publish`, `pages_show_list`, and `instagram_basic` permissions, for the Page connected to your IG account.
5. Find your `IG_BUSINESS_ACCOUNT_ID` by calling `GET /{page-id}?fields=instagram_business_account&access_token=...`.
6. Put both values in `.env`.

Access tokens from the Graph API Explorer expire in ~1 hour — for anything scheduled, generate a **long-lived token** (60 days, renewable) or set up a server-side token refresh. This is the fiddliest part of the whole setup; budget time for it.

#### Image hosting (required for publishing)

The Graph API fetches the image from a URL you give it — it won't accept a local file upload. Point `IMAGE_HOST_UPLOAD_URL` / `IMAGE_HOST_PUBLIC_BASE_URL` at any host you control (S3 + public bucket, Cloudinary, a small static file server). `signal_europe/publish/graph_api.py::upload_image()` is a thin wrapper — swap its body for whatever your chosen host's upload API expects.

#### IG account watching (optional)

Only needed if you want the `scaling.europe`/`vesting` watcher to use a logged-in session (more headroom before rate limits than anonymous). **Use a throwaway account, never your real Signal Europe login** — this path violates Instagram's ToS and risks the account it's logged in with.

### 3. Run it

```bash
python main.py
```

Each run: fetches new items, skips anything already processed (tracked in `data/seen_items.db`), renders graphics into `output/review_queue/` (or publishes if `AUTO_PUBLISH=true`), and prints a summary.

### 4. Schedule it

Run on a schedule with cron, e.g. every 2 hours:

```
0 */2 * * * cd /path/to/SIGNAL-EUROPE && .venv/bin/python main.py >> logs/pipeline.log 2>&1
```

(create a `logs/` dir first). Any scheduler works — systemd timer, launchd, a GitHub Actions cron workflow, etc.

## Branding

Drop your real brand fonts into `assets/fonts/` as `Bold.ttf` and `Regular.ttf` — the generator uses them automatically if present. It currently ships with DejaVu Sans as a placeholder (freely licensed, supports € and other European characters — the built-in Pillow fallback font doesn't). Layout, colors, and text placement live in `signal_europe/content/image_gen.py::render()`.

## Adding/removing sources

- RSS feeds: edit `config/feeds.yaml`.
- Watched IG accounts: edit `config/watched_accounts.yaml`.

## Known limitations / things to watch

- The Instagram watcher can break whenever Instagram changes its web interface, or gets your watcher IP/account rate-limited. Treat it as a bonus signal, not the backbone — the RSS feeds are what keeps content flowing if it stops working.
- Graph API access tokens expire; you'll need to refresh periodically unless you build a refresh-token flow.
- The image template is intentionally simple — extend `image_gen.py` with your actual brand identity (logo, colors, fonts) before going live for real.
