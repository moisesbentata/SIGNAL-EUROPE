"""Orchestrates one run of the pipeline:

  1. Pull new items from RSS feeds (reliable) and watched IG accounts (best-effort)
  2. Drop anything already seen
  3. Render each new item into a branded graphic
  4. If AUTO_PUBLISH=true, publish immediately via the Graph API.
     Otherwise leave it in output/review_queue/ for a human to check and
     publish manually (safe default).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from signal_europe.content import image_gen
from signal_europe.publish import graph_api
from signal_europe.sources import instagram_source, rss_source
from signal_europe.state import SeenItemStore

REVIEW_QUEUE_DIR = Path("output/review_queue")


def _build_caption(item) -> str:
    parts = [item.headline]
    if item.summary and item.summary != item.headline:
        parts.append(item.summary)
    if item.url:
        parts.append(f"Source: {item.url}")
    parts.append("#EuropeanVC #AI #Startups #SignalEurope")
    return "\n\n".join(parts)


def run(auto_publish: bool = None) -> list:
    load_dotenv()
    if auto_publish is None:
        auto_publish = os.getenv("AUTO_PUBLISH", "false").strip().lower() == "true"

    store = SeenItemStore()
    all_items = []

    try:
        all_items.extend(rss_source.fetch_new_items())
    except Exception as exc:  # noqa: BLE001
        print(f"[pipeline] rss_source failed: {exc}")

    try:
        all_items.extend(instagram_source.fetch_new_items())
    except Exception as exc:  # noqa: BLE001
        print(f"[pipeline] instagram_source failed: {exc}")

    new_items = [i for i in all_items if not store.is_seen(i.source, i.item_id)]
    print(f"[pipeline] {len(all_items)} fetched, {len(new_items)} new")

    results = []
    for item in new_items:
        image_path = image_gen.render(item, output_dir=REVIEW_QUEUE_DIR)
        caption = _build_caption(item)
        outcome = {"item": item, "image_path": image_path, "published": False}

        if auto_publish:
            try:
                image_url = graph_api.upload_image(str(image_path))
                media_id = graph_api.publish_image(image_url, caption)
                outcome["published"] = True
                outcome["media_id"] = media_id
                print(f"[pipeline] published {item.source}/{item.item_id} -> {media_id}")
            except Exception as exc:  # noqa: BLE001
                print(f"[pipeline] publish failed for {item.source}/{item.item_id}: {exc}")
        else:
            caption_path = image_path.with_suffix(".caption.txt")
            caption_path.write_text(caption, encoding="utf-8")
            print(f"[pipeline] queued for review: {image_path}")

        store.mark_seen(item.source, item.item_id)
        results.append(outcome)

    return results
