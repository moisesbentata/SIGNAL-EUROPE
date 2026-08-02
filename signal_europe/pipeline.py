"""Orchestrates one run of the pipeline:

  1. Pull new items from RSS feeds (reliable), watched IG accounts
     (best-effort), and X/Twitter search (optional, official API only)
  2. Drop anything already seen, and anything not European company/tech/AI/VC
     news (signal_europe/filters.py)
  3. Rank what's left and take the best items up to the remaining daily
     posting quota (AUTO_PUBLISH_MIN_PER_DAY / AUTO_PUBLISH_MAX_PER_DAY)
  4. Render each into a branded slide carousel (1 headline + up to 2 detail
     slides)
  5. If AUTO_PUBLISH=true, publish immediately via the Graph API as a
     carousel post. Otherwise leave the slides in output/review_queue/ for
     a human to check and publish manually (safe default).

Run this on a schedule (see README) — each run only posts up to whatever
quota is left for the day, so scheduling it every 1-2 hours naturally
spreads posts across the day instead of dumping them all at once.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from signal_europe.content import image_gen
from signal_europe.filters import filter_items
from signal_europe.publish import graph_api
from signal_europe.ranking import rank_items
from signal_europe.sources import instagram_source, rss_source, twitter_source
from signal_europe.state import SeenItemStore

REVIEW_QUEUE_DIR = Path("output/review_queue")

DEFAULT_MAX_POSTS_PER_DAY = 4


def _build_caption(item) -> str:
    parts = [item.headline.replace("**", "")]
    if item.summary and item.summary != item.headline:
        parts.append(item.summary)
    if item.url:
        parts.append(f"Source: {item.url}")
    parts.append("#EuropeanVC #AI #Startups #SignalEurope")
    return "\n\n".join(parts)


def _fetch_all_items() -> list:
    all_items = []
    for name, fetch in (
        ("rss_source", rss_source.fetch_new_items),
        ("instagram_source", instagram_source.fetch_new_items),
        ("twitter_source", twitter_source.fetch_new_items),
    ):
        try:
            all_items.extend(fetch())
        except Exception as exc:  # noqa: BLE001 - one bad source shouldn't kill the run
            print(f"[pipeline] {name} failed: {exc}")
    return all_items


def run(auto_publish: bool = None) -> list:
    load_dotenv()
    if auto_publish is None:
        auto_publish = os.getenv("AUTO_PUBLISH", "false").strip().lower() == "true"
    max_per_day = int(os.getenv("MAX_POSTS_PER_DAY", DEFAULT_MAX_POSTS_PER_DAY))

    store = SeenItemStore()

    all_items = _fetch_all_items()
    new_items = [i for i in all_items if not store.is_seen(i.source, i.item_id)]
    on_brand_items = filter_items(new_items)
    ranked_items = rank_items(on_brand_items)

    remaining_quota = max(0, max_per_day - store.posts_today())
    print(
        f"[pipeline] {len(all_items)} fetched, {len(new_items)} new, "
        f"{len(on_brand_items)} on-brand, {remaining_quota} slots left today"
    )

    selected = ranked_items[:remaining_quota]
    if not selected:
        print("[pipeline] nothing to do this run (no on-brand items or daily quota reached)")
        return []

    results = []
    for item in selected:
        slide_paths = image_gen.render_carousel(item, output_dir=REVIEW_QUEUE_DIR)
        caption = _build_caption(item)
        outcome = {"item": item, "slide_paths": slide_paths, "published": False}

        if auto_publish:
            try:
                image_urls = [graph_api.upload_image(str(p)) for p in slide_paths]
                if len(image_urls) == 1:
                    media_id = graph_api.publish_image(image_urls[0], caption)
                else:
                    media_id = graph_api.publish_carousel(image_urls, caption)
                outcome["published"] = True
                outcome["media_id"] = media_id
                print(f"[pipeline] published {item.source}/{item.item_id} -> {media_id}")
            except Exception as exc:  # noqa: BLE001
                print(f"[pipeline] publish failed for {item.source}/{item.item_id}: {exc}")
        else:
            caption_path = slide_paths[0].with_name(slide_paths[0].stem.rsplit("__", 1)[0] + ".caption.txt")
            caption_path.write_text(caption, encoding="utf-8")
            print(f"[pipeline] queued {len(slide_paths)}-slide carousel for review: {slide_paths[0].parent}")

        # counts toward today's quota whether it was auto-published or
        # queued for manual review — either way a "slot" was used
        store.increment_posts_today()
        store.mark_seen(item.source, item.item_id)
        results.append(outcome)

    return results
