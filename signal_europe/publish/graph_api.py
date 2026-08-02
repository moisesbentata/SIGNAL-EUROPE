"""Official Instagram publishing via the Meta Graph API.

Publishing a single photo is a two-step call:
  1. POST /{ig_user_id}/media       with image_url + caption -> creation_id
  2. POST /{ig_user_id}/media_publish with creation_id        -> published post

Publishing a carousel (2-10 images/slides in one post) adds a layer:
  1. POST /{ig_user_id}/media  per image, with is_carousel_item=true (no
     caption on the children) -> one creation_id per slide
  2. POST /{ig_user_id}/media  with media_type=CAROUSEL, children=<ids>,
     and the caption -> a parent creation_id
  3. POST /{ig_user_id}/media_publish with the parent creation_id

The Graph API requires each image at a public URL (it fetches it
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


def _get_credentials():
    ig_user_id = os.getenv("IG_BUSINESS_ACCOUNT_ID")
    access_token = os.getenv("IG_ACCESS_TOKEN")
    if not ig_user_id or not access_token:
        raise GraphAPIError(
            "IG_BUSINESS_ACCOUNT_ID / IG_ACCESS_TOKEN are not configured in .env"
        )
    return ig_user_id, access_token


def _wait_until_finished(creation_id: str, access_token: str) -> None:
    for _ in range(10):
        status_resp = requests.get(
            f"{GRAPH_API_BASE}/{creation_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        status_code = status_resp.json().get("status_code")
        if status_code == "FINISHED":
            return
        time.sleep(2)
    raise GraphAPIError(f"media container {creation_id} never finished processing")


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
    ig_user_id, access_token = _get_credentials()

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

    _wait_until_finished(creation_id, access_token)

    publish_resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": access_token},
        timeout=30,
    )
    if not publish_resp.ok:
        raise GraphAPIError(f"media publish failed: {publish_resp.text}")

    return publish_resp.json()["id"]


def publish_carousel(image_urls: list, caption: str) -> str:
    """Publishes a multi-slide carousel post (2-10 images). Returns the
    published media id. image_urls must be public URLs in slide order —
    upload each local slide with upload_image() first."""
    if not 2 <= len(image_urls) <= 10:
        raise GraphAPIError(f"carousel needs 2-10 images, got {len(image_urls)}")

    ig_user_id, access_token = _get_credentials()

    child_ids = []
    for image_url in image_urls:
        child_resp = requests.post(
            f"{GRAPH_API_BASE}/{ig_user_id}/media",
            data={
                "image_url": image_url,
                "is_carousel_item": "true",
                "access_token": access_token,
            },
            timeout=30,
        )
        if not child_resp.ok:
            raise GraphAPIError(f"carousel child creation failed: {child_resp.text}")
        child_id = child_resp.json()["id"]
        _wait_until_finished(child_id, access_token)
        child_ids.append(child_id)

    parent_resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media",
        data={
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": access_token,
        },
        timeout=30,
    )
    if not parent_resp.ok:
        raise GraphAPIError(f"carousel container creation failed: {parent_resp.text}")
    parent_id = parent_resp.json()["id"]

    _wait_until_finished(parent_id, access_token)

    publish_resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
        data={"creation_id": parent_id, "access_token": access_token},
        timeout=30,
    )
    if not publish_resp.ok:
        raise GraphAPIError(f"carousel publish failed: {publish_resp.text}")

    return publish_resp.json()["id"]
