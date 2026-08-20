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
    """Uploads a local file to the configured video host and returns its
    public URL. Requires VIDEO_HOST_UPLOAD_URL / VIDEO_HOST_PUBLIC_BASE_URL
    to be set to an endpoint you control — swap the body of this function
    for whatever that host's upload API expects."""
    upload_url = os.getenv("VIDEO_HOST_UPLOAD_URL")
    public_base = os.getenv("VIDEO_HOST_PUBLIC_BASE_URL")
    if not upload_url or not public_base:
        raise GraphAPIError(
            "VIDEO_HOST_UPLOAD_URL / VIDEO_HOST_PUBLIC_BASE_URL are not configured. "
            "The Graph API needs a public video URL — point these at a video "
            "host you control before publishing."
        )
    with open(local_path, "rb") as f:
        resp = requests.post(upload_url, files={"file": f}, timeout=120)
    resp.raise_for_status()
    filename = os.path.basename(local_path)
    return f"{public_base.rstrip('/')}/{filename}"


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
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        status_code = status_resp.json().get("status_code")
        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            raise GraphAPIError(f"media container {creation_id} failed processing")
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
