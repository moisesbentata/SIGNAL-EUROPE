"""Official Instagram publishing via the Meta Graph API.

Publishing a photo is a two-step call:
  1. POST /{ig_user_id}/media       with image_url + caption -> creation_id
  2. POST /{ig_user_id}/media_publish with creation_id        -> published post

The Graph API requires the image at a public URL (it fetches it
server-side) — local files must be uploaded somewhere reachable first,
see upload_image() below.

Setup required before this works (see README.md):
  - IG account converted to Business/Creator and linked to a Facebook Page
  - Meta Developer App with instagram_content_publish permission
  - IG_BUSINESS_ACCOUNT_ID + IG_ACCESS_TOKEN in .env
"""

import os
import time

import requests

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


class GraphAPIError(RuntimeError):
    pass


def upload_image(local_path: str) -> str:
    """Uploads a local file to the configured image host and returns its
    public URL. Requires IMAGE_HOST_UPLOAD_URL / IMAGE_HOST_PUBLIC_BASE_URL
    to be set to an endpoint you control (S3, Cloudinary, your own static
    server, etc) — swap the body of this function for whatever that host's
    API expects.
    """
    upload_url = os.getenv("IMAGE_HOST_UPLOAD_URL")
    public_base = os.getenv("IMAGE_HOST_PUBLIC_BASE_URL")
    if not upload_url or not public_base:
        raise GraphAPIError(
            "IMAGE_HOST_UPLOAD_URL / IMAGE_HOST_PUBLIC_BASE_URL are not configured. "
            "The Graph API needs a public image URL — point these at an image "
            "host you control before publishing."
        )
    with open(local_path, "rb") as f:
        resp = requests.post(upload_url, files={"file": f}, timeout=30)
    resp.raise_for_status()
    filename = os.path.basename(local_path)
    return f"{public_base.rstrip('/')}/{filename}"


def publish_image(image_url: str, caption: str) -> str:
    """Publishes a single image post. Returns the published media id."""
    ig_user_id = os.getenv("IG_BUSINESS_ACCOUNT_ID")
    access_token = os.getenv("IG_ACCESS_TOKEN")
    if not ig_user_id or not access_token:
        raise GraphAPIError(
            "IG_BUSINESS_ACCOUNT_ID / IG_ACCESS_TOKEN are not configured in .env"
        )

    create_resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media",
        data={
            "image_url": image_url,
            "caption": caption,
            "access_token": access_token,
        },
        timeout=30,
    )
    if not create_resp.ok:
        raise GraphAPIError(f"media creation failed: {create_resp.text}")
    creation_id = create_resp.json()["id"]

    # container processing is async, poll briefly before publishing
    for _ in range(10):
        status_resp = requests.get(
            f"{GRAPH_API_BASE}/{creation_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        status_code = status_resp.json().get("status_code")
        if status_code == "FINISHED":
            break
        time.sleep(2)
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
