# Signal Europe — Instagram content pipeline

Automates content for the `Signal Europe` Instagram account (European VC &
AI). It watches news sources, filters for European tech/AI/VC relevance,
turns the best items into branded slide carousels, and either queues them
for you to review or auto-publishes them — at most a few times a day.

## How it works

```
RSS feeds (reliable)      ─┐
Watched IG accounts (best-effort) ├─> dedup -> European/tech filter -> rank -> daily quota
X/Twitter search (optional, paid) ┘         (SQLite)   (filters.py)   (ranking.py)  (state.py)
                                                                              │
                                                                              v
                                          branded slide carousel (Pillow) -> review_queue/ or auto-publish (Graph API)
```

- **RSS feeds** (`config/feeds.yaml`) — EU-Startups, Sifted, Tech.eu, and TechCrunch's Europe tag are already Europe-scoped (`europe_focused: true`, skip the filter). General TechCrunch, The Verge, and Business Insider are also pulled in for broader tech coverage, then run through the European relevance filter below. Official, reliable, no ToS risk.
- **European/tech relevance filter** (`signal_europe/filters.py`) — keyword heuristic that keeps only items about European companies/markets *and* tech/AI/VC topics. Items from the already-Europe-scoped feeds skip this check; everything else (general TechCrunch, Verge, Business Insider, Twitter) has to pass it. It's a heuristic, not perfect — tune the keyword lists as you see false positives/negatives land in the review queue.
- **Ranking** (`signal_europe/ranking.py`) — when there's more on-brand news than the daily cadence allows, scores items (funding figures, named rounds, AI mentions, acquisitions) and keeps the best ones.
- **Posting cadence** (`MAX_POSTS_PER_DAY` in `.env`, default 4) — each run only takes as many top-ranked items as are left in the day's quota (tracked in SQLite), so scheduling the pipeline every 1-2 hours naturally spreads 2-4 posts across the day instead of dumping everything at once. There's no way to *guarantee* a minimum of 2/day — that depends on there being that much real on-brand news — but this is the mechanism that would deliver it when there is.
- **Watched IG accounts** (`config/watched_accounts.yaml`) — currently `scaling.europe` and `vesting`. Uses `instaloader` against Instagram's public web interface. **This is unofficial and against Instagram's Terms of Service.** It can be rate-limited or blocked at any time without warning. Only caption *text* is read — the source accounts' images/videos are never copied or reposted.
- **X/Twitter search** (`config/twitter_watch.yaml`, optional) — official X API v2 recent search. Disabled by default (no-op until `TWITTER_BEARER_TOKEN` is set). X's unofficial scraping libraries are all broken as of the 2023 API lockdown, so this is the only reliable route, and it requires a paid developer tier (Basic, ~$200/mo as of writing — check current pricing before enabling). The RSS feeds already cover most of the same funding/launch news for free.
- **Image generation** (`signal_europe/content/image_gen.py`) — every post is a fresh slide carousel (1 headline slide + up to 2 detail slides) rendered from extracted text onto Signal Europe's own template: gradient background, stars-arc/wordmark logo, "Breaking News" pill, circular company badge, yellow-highlighted headline, "Read More" button. Nothing from a source's actual image is ever reused, which keeps this on the right side of copyright.
- **Publishing** (`signal_europe/publish/graph_api.py`) — via Meta's official Instagram Graph API, as a multi-image carousel post. The only sanctioned way to post programmatically.

By default the pipeline does **not** auto-publish — it writes each candidate carousel's slides + caption to `output/review_queue/` so you can check before anything goes live. Flip `AUTO_PUBLISH=true` in `.env` once you trust it.

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

The Graph API fetches each slide from a URL you give it — it won't accept a local file upload. Point `IMAGE_HOST_UPLOAD_URL` / `IMAGE_HOST_PUBLIC_BASE_URL` at any host you control (S3 + public bucket, Cloudinary, a small static file server). `signal_europe/publish/graph_api.py::upload_image()` is a thin wrapper — swap its body for whatever your chosen host's upload API expects.

#### IG account watching (optional)

Only needed if you want the `scaling.europe`/`vesting` watcher to use a logged-in session (more headroom before rate limits than anonymous). **Use a throwaway account, never your real Signal Europe login** — this path violates Instagram's ToS and risks the account it's logged in with.

#### X/Twitter watching (optional, paid)

Set `TWITTER_BEARER_TOKEN` in `.env` if you have an X developer account with recent-search access (Basic tier or above). Edit the search query in `config/twitter_watch.yaml`. Leave it blank to skip Twitter entirely — the pipeline runs fine without it.

### 3. Real logo and brand fonts

The template currently draws a programmatic approximation of the SGNL Europe logo (stars arc + wordmark) since it doesn't have your actual logo file. Drop the real transparent PNG at `assets/logo/logo.png` and it'll be used automatically instead — no code changes needed. Same for fonts: put `Bold.ttf` / `Regular.ttf` in `assets/fonts/` to replace the bundled DejaVu Sans placeholder (chosen because it renders € and other European characters correctly, which the default Pillow fallback font doesn't).

### 4. Run it

```bash
python main.py
```

Each run: fetches new items from all sources, drops anything already processed (tracked in `data/seen_items.db`) or off-brand (not European tech/AI/VC), ranks what's left, takes the best items up to today's remaining quota, renders each into a slide carousel in `output/review_queue/` (or publishes if `AUTO_PUBLISH=true`), and prints a summary.

### 5. Schedule it

Run on a schedule with cron, e.g. every 2 hours:

```
0 */2 * * * cd /path/to/SIGNAL-EUROPE && .venv/bin/python main.py >> logs/pipeline.log 2>&1
```

(create a `logs/` dir first). Any scheduler works — systemd timer, launchd, a GitHub Actions cron workflow, etc. Running it every 1-2 hours (rather than once a day) is what lets the daily quota actually spread posts through the day instead of grabbing all of them in one burst.

## Adding/removing sources

- RSS feeds: edit `config/feeds.yaml`. Mark a feed `europe_focused: true` if it's already scoped to Europe (skips the relevance filter); leave it `false` for broad/global feeds that need filtering.
- Watched IG accounts: edit `config/watched_accounts.yaml`.
- Twitter search query: edit `config/twitter_watch.yaml`.
- Relevance filter keywords: edit the lists at the top of `signal_europe/filters.py`.
- Ranking weights: edit `signal_europe/ranking.py::score_item()`.
- Posting cadence: `MAX_POSTS_PER_DAY` in `.env`.

## Known limitations / things to watch

- The Instagram watcher can break whenever Instagram changes its web interface, or gets your watcher IP/account rate-limited. Treat it as a bonus signal, not the backbone — the RSS feeds are what keeps content flowing if it stops working.
- Twitter search requires a paid X developer tier; it's entirely optional and the pipeline works without it.
- The European/tech relevance filter is a keyword heuristic — it will occasionally let through something off-topic or drop something that should have qualified. Check the review queue periodically and tune `filters.py`.
- Graph API access tokens expire; you'll need to refresh periodically unless you build a refresh-token flow.
- There's no way to *force* a minimum posts/day if there genuinely isn't enough on-brand news — the cadence mechanism only caps the maximum.
