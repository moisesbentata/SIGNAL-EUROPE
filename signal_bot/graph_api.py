"""Official Instagram publishing via the Meta Graph API.

Publishing a Reel is a two-step call, same shape as a photo but with a
longer processing wait since video needs to be transcoded server-side:
  1. POST /{ig_user_id}/media  with media_type=REELS, video_url, caption
     -> creation_id
  2. POST /{ig_user_id}/media_publish with creation_id -> published post

The Graph API fetches the video from a URL you give it — a local file
must be uploaded somewhere reachable first, see upload_video() below.

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


def upload_video(local_path: str) -> str:
    """Uploads a local video to Cloudinary and returns its public HTTPS
    URL. Reads credentials from the CLOUDINARY_URL env var (format:
    cloudinary://<api_key>:<api_secret>@<cloud_name>) — grab that value
    verbatim from Cloudinary dashboard → Product Environment → API
    environment variable."""
    if not os.getenv("CLOUDINARY_URL"):
        raise GraphAPIError(
            "CLOUDINARY_URL is not configured. Get it from your Cloudinary "
            "dashboard and set it as a Railway variable."
        )
    import cloudinary.uploader  # imported lazily to keep the dep optional

    try:
        result = cloudinary.uploader.upload_large(
            local_path,
            resource_type="video",
            chunk_size=6_000_000,  # 6 MB chunks, safe for Cloudinary's limit
        )
    except Exception as exc:  # noqa: BLE001
        raise GraphAPIError(f"Cloudinary upload failed: {exc}") from exc

    url = result.get("secure_url")
    if not url:
        raise GraphAPIError(f"Cloudinary upload returned no secure_url: {result}")
    return url


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


def publish_reel(video_url: str, caption: str) -> str:
    """Publishes a single Reel. Returns the published media id."""
    ig_user_id, access_token = _get_credentials()

    create_resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": access_token,
        },
        timeout=30,
    )
    if not create_resp.ok:
        raise GraphAPIError(f"media creation failed: {create_resp.text}")
    creation_id = create_resp.json()["id"]

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
            break
        if status_code == "ERROR":
            raise GraphAPIError(
                f"media container {creation_id} failed processing — "
                f"status_code=ERROR, detail={status_detail!r}"
            )
        time.sleep(VIDEO_POLL_INTERVAL_SECONDS)
    else:
        raise GraphAPIError(f"media container {creation_id} never finished processing")

    publish_resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": access_token},
        timeout=30,
    )
    if not publish_resp.ok:
        raise GraphAPIError(f"media publish failed: {publish_resp.text}")

    return publish_resp.json()["id"]
