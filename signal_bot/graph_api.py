"""Official Instagram publishing via the Meta Graph API.

Supports two shapes of post the account can publish programmatically:

  * Single Reel (video only): POST /media with media_type=REELS +
    video_url, wait for the container to finish processing, then POST
    /media_publish.

  * Feed carousel (2-10 mixed images/videos): POST /media with
    is_carousel_item=true per child (image_url for images, media_type=
    VIDEO + video_url for videos), then a parent POST /media with
    media_type=CAROUSEL + children=<child_ids>, then /media_publish.
    Instagram does not allow carousels to appear as Reels; a carousel
    is always a feed post.

The Graph API fetches the media from a public URL you provide — a
local file must be uploaded somewhere reachable first, see
upload_media() below (Cloudinary-backed).

Setup required (see README.md):
  - IG account converted to Business/Creator and linked to a Facebook Page
  - Meta Developer App with instagram_content_publish permission
  - IG_BUSINESS_ACCOUNT_ID + IG_ACCESS_TOKEN in .env
"""

import os
import time

import requests

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"

# video processing takes longer than a photo container; poll for longer
VIDEO_POLL_ATTEMPTS = 30
VIDEO_POLL_INTERVAL_SECONDS = 5


class GraphAPIError(RuntimeError):
    pass


def _get_credentials():
    ig_user_id = os.getenv("IG_BUSINESS_ACCOUNT_ID")
    access_token = os.getenv("IG_ACCESS_TOKEN")
    if not ig_user_id or not access_token:
        raise GraphAPIError(
            "IG_BUSINESS_ACCOUNT_ID / IG_ACCESS_TOKEN are not configured in .env"
        )
    return ig_user_id, access_token


def upload_media(local_path: str, media_type: str = "video") -> str:
    """Uploads a local image or video to Cloudinary and returns its
    public HTTPS URL. media_type must be "image" or "video".

    Reads credentials from the CLOUDINARY_URL env var (format:
    cloudinary://<api_key>:<api_secret>@<cloud_name>) — grab that value
    verbatim from Cloudinary dashboard → Product Environment → API
    environment variable.
    """
    if media_type not in ("image", "video"):
        raise GraphAPIError(f"unsupported media_type {media_type!r}")
    if not os.getenv("CLOUDINARY_URL"):
        raise GraphAPIError(
            "CLOUDINARY_URL is not configured. Get it from your Cloudinary "
            "dashboard and set it as a Railway variable."
        )
    import cloudinary.uploader  # imported lazily to keep the dep optional

    try:
        # upload_large chunks big files (needed for video); works fine for
        # images too, just chunks less often
        result = cloudinary.uploader.upload_large(
            local_path,
            resource_type=media_type,
            chunk_size=6_000_000,  # 6 MB chunks, safe for Cloudinary's limit
        )
    except Exception as exc:  # noqa: BLE001
        raise GraphAPIError(f"Cloudinary upload failed: {exc}") from exc

    url = result.get("secure_url")
    if not url:
        raise GraphAPIError(f"Cloudinary upload returned no secure_url: {result}")
    return url


# Backward-compatible alias — old call sites still use upload_video.
def upload_video(local_path: str) -> str:
    return upload_media(local_path, media_type="video")


def get_media_permalink(ig_media_id: str) -> str:
    """Fetches the public instagram.com/p/... permalink for a media
    the account has already published. Returns "" if the API says no."""
    _, access_token = _get_credentials()
    try:
        resp = requests.get(
            f"{GRAPH_API_BASE}/{ig_media_id}",
            params={"fields": "permalink", "access_token": access_token},
            timeout=30,
        )
        if not resp.ok:
            return ""
        return resp.json().get("permalink", "") or ""
    except Exception:  # noqa: BLE001
        return ""


def get_media_reach(ig_media_id: str) -> int:
    """Fetches reach for a published media item. Returns 0 on any error
    or if the insight isn't available yet (Instagram usually needs a few
    hours after publish before reach numbers stabilize)."""
    _, access_token = _get_credentials()
    resp = requests.get(
        f"{GRAPH_API_BASE}/{ig_media_id}/insights",
        params={"metric": "reach", "access_token": access_token},
        timeout=30,
    )
    if not resp.ok:
        return 0
    try:
        data = resp.json().get("data", [])
        for row in data:
            if row.get("name") == "reach":
                values = row.get("values", [])
                if values:
                    return int(values[0].get("value", 0))
    except (ValueError, TypeError, KeyError):
        return 0
    return 0


def _as_jpeg_url(cloudinary_url: str) -> str:
    """Inserts a Cloudinary f_jpg,q_auto transformation so the fetched
    image is a JPEG regardless of the original upload format. Graph
    API's IMAGE endpoints require JPEG specifically."""
    return cloudinary_url.replace("/image/upload/", "/image/upload/f_jpg,q_auto/", 1)


def _wait_finished(creation_id: str, access_token: str) -> None:
    for _ in range(VIDEO_POLL_ATTEMPTS):
        status_resp = requests.get(
            f"{GRAPH_API_BASE}/{creation_id}",
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=30,
        )
        payload = status_resp.json()
        status_code = payload.get("status_code")
        status_detail = payload.get("status", "")
        if status_code == "FINISHED":
            return
        if status_code == "ERROR":
            raise GraphAPIError(
                f"media container {creation_id} failed processing — "
                f"status_code=ERROR, detail={status_detail!r}"
            )
        time.sleep(VIDEO_POLL_INTERVAL_SECONDS)
    raise GraphAPIError(f"media container {creation_id} never finished processing")


def _create_container(payload: dict, access_token: str, ig_user_id: str) -> str:
    resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media",
        data={**payload, "access_token": access_token},
        timeout=30,
    )
    if not resp.ok:
        raise GraphAPIError(f"media creation failed: {resp.text}")
    return resp.json()["id"]


def _media_publish(creation_id: str, access_token: str, ig_user_id: str) -> str:
    publish_resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": access_token},
        timeout=30,
    )
    if not publish_resp.ok:
        raise GraphAPIError(f"media publish failed: {publish_resp.text}")
    return publish_resp.json()["id"]


def publish_reel(video_url: str, caption: str) -> str:
    """Publishes a single Reel. Returns the published media id."""
    ig_user_id, access_token = _get_credentials()
    creation_id = _create_container(
        {"media_type": "REELS", "video_url": video_url, "caption": caption},
        access_token, ig_user_id,
    )
    _wait_finished(creation_id, access_token)
    return _media_publish(creation_id, access_token, ig_user_id)


def publish_photo(image_url: str, caption: str) -> str:
    """Publishes a single-image feed post. Returns the published media
    id. image_url must be JPEG per Graph API requirements — pass a
    Cloudinary URL and _as_jpeg_url() will convert it."""
    ig_user_id, access_token = _get_credentials()
    creation_id = _create_container(
        {"image_url": _as_jpeg_url(image_url), "caption": caption},
        access_token, ig_user_id,
    )
    _wait_finished(creation_id, access_token)
    return _media_publish(creation_id, access_token, ig_user_id)


def publish_carousel(items: list, caption: str) -> str:
    """Publishes a feed carousel post (2-10 mixed image/video items).

    items = [{"url": "https://...", "type": "image"|"video"}, ...] in
    the exact order they should appear as slides. Returns the published
    parent media id.

    Note: Instagram doesn't allow carousels to appear as Reels — even
    an all-video carousel lands as a feed post. That's per Meta, not a
    limitation of this code.
    """
    if not 2 <= len(items) <= 10:
        raise GraphAPIError(f"carousel needs 2-10 items, got {len(items)}")

    ig_user_id, access_token = _get_credentials()

    child_ids = []
    for i, item in enumerate(items):
        if item["type"] == "image":
            payload = {
                "image_url": _as_jpeg_url(item["url"]),
                "is_carousel_item": "true",
            }
        elif item["type"] == "video":
            payload = {
                "media_type": "VIDEO",
                "video_url": item["url"],
                "is_carousel_item": "true",
            }
        else:
            raise GraphAPIError(f"unsupported carousel item type: {item['type']!r}")
        try:
            child_id = _create_container(payload, access_token, ig_user_id)
            _wait_finished(child_id, access_token)
        except GraphAPIError as exc:
            raise GraphAPIError(f"carousel child #{i + 1} failed: {exc}") from exc
        child_ids.append(child_id)

    parent_id = _create_container(
        {
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
        },
        access_token, ig_user_id,
    )
    _wait_finished(parent_id, access_token)
    return _media_publish(parent_id, access_token, ig_user_id)
