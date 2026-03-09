"""
linkedin_poster.py
Posts text (+ optional image) to LinkedIn API v2.

Image posting is a 3-step process:
  1. Register upload  → get asset URN + upload URL
  2. PUT image bytes  → upload to LinkedIn's CDN
  3. POST ugcPost     → publish referencing the asset URN

Text-only posting skips steps 1–2.

Raises:
  PermissionError   — on HTTP 401 (token expired or invalid)
  requests.HTTPError — on other API failures
"""

import requests
from loguru import logger

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
REGISTER_UPLOAD_URL = f"{LINKEDIN_API_BASE}/assets?action=registerUpload"
UGC_POSTS_URL = f"{LINKEDIN_API_BASE}/ugcPosts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _check_401(response: requests.Response) -> None:
    if response.status_code == 401:
        raise PermissionError(
            "LinkedIn token expired or invalid (HTTP 401). "
            "Generate a new OAuth access token and update LINKEDIN_ACCESS_TOKEN in .env. "
            "See workflows/linkedin_pipeline.md for instructions."
        )


# ---------------------------------------------------------------------------
# Image upload (3-step)
# ---------------------------------------------------------------------------

def _register_image_upload(access_token: str, person_urn: str) -> tuple[str, str]:
    """
    Step 1: Register image upload with LinkedIn.
    Returns (asset_urn, upload_url).
    """
    payload = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": person_urn,
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }
            ],
        }
    }
    logger.debug("LinkedIn: registering image upload")
    resp = requests.post(
        REGISTER_UPLOAD_URL,
        headers=_auth_headers(access_token),
        json=payload,
        timeout=30,
    )
    _check_401(resp)
    resp.raise_for_status()

    data = resp.json()
    asset_urn = data["value"]["asset"]
    upload_url = (
        data["value"]["uploadMechanism"]
        ["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]
        ["uploadUrl"]
    )
    logger.debug(f"LinkedIn: asset URN = {asset_urn}")
    return asset_urn, upload_url


def _upload_image_bytes(upload_url: str, image_url: str, access_token: str) -> None:
    """
    Step 2: Download image from imgbb and PUT bytes to LinkedIn's upload URL.
    """
    logger.debug(f"Downloading image from: {image_url}")
    img_resp = requests.get(image_url, timeout=60)
    img_resp.raise_for_status()
    image_bytes = img_resp.content

    content_type = img_resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
    logger.debug(f"Uploading {len(image_bytes)} bytes to LinkedIn (content-type: {content_type})")

    upload_resp = requests.put(
        upload_url,
        data=image_bytes,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": content_type,
        },
        timeout=60,
    )
    upload_resp.raise_for_status()
    logger.debug("LinkedIn: image upload complete")


# ---------------------------------------------------------------------------
# Post creation
# ---------------------------------------------------------------------------

def _create_text_post(access_token: str, person_urn: str, post_body: str) -> str:
    """Create a text-only LinkedIn post. Returns the post URN."""
    payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": post_body},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }
    resp = requests.post(
        UGC_POSTS_URL,
        headers=_auth_headers(access_token),
        json=payload,
        timeout=30,
    )
    _check_401(resp)
    resp.raise_for_status()
    return resp.headers.get("X-RestLi-Id", "unknown")


def _create_image_post(
    access_token: str, person_urn: str, post_body: str, asset_urn: str
) -> str:
    """Create a LinkedIn post with an attached image. Returns the post URN."""
    payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": post_body},
                "shareMediaCategory": "IMAGE",
                "media": [
                    {
                        "status": "READY",
                        "media": asset_urn,
                    }
                ],
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }
    resp = requests.post(
        UGC_POSTS_URL,
        headers=_auth_headers(access_token),
        json=payload,
        timeout=30,
    )
    _check_401(resp)
    resp.raise_for_status()
    return resp.headers.get("X-RestLi-Id", "unknown")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def post_to_linkedin(post_body: str, image_url: str | None, config: dict) -> str:
    """
    Post to LinkedIn with or without an image.

    Args:
        post_body:  Full post text (hook + text + cta + hashtags).
        image_url:  imgbb URL string, or None for text-only post.
        config:     Loaded config dict (must have linkedin_posting section).

    Returns:
        The LinkedIn UGC post URN string.

    Raises:
        PermissionError on HTTP 401 (expired token).
        requests.HTTPError on other API failures.
    """
    li_cfg = config["linkedin_posting"]
    access_token = li_cfg["access_token"]
    person_urn = li_cfg["person_urn"]

    if image_url:
        logger.info("LinkedIn: posting with image (3-step upload)")
        asset_urn, upload_url = _register_image_upload(access_token, person_urn)
        _upload_image_bytes(upload_url, image_url, access_token)
        post_urn = _create_image_post(access_token, person_urn, post_body, asset_urn)
    else:
        logger.info("LinkedIn: posting text-only")
        post_urn = _create_text_post(access_token, person_urn, post_body)

    logger.success(f"LinkedIn post published | URN: {post_urn}")
    return post_urn
