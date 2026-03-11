"""
telegram_notifier.py
Sends generated LinkedIn post + 3 images to Telegram via Bot API.
Stores pending post in .tmp/pending_posts.json for callback handling by telegram_bot.py.
"""

import os
import json
import requests
from datetime import datetime, timezone
from loguru import logger

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
PENDING_POSTS_PATH = ".tmp/pending_posts.json"


def _api(token: str, method: str, payload: dict) -> dict:
    url = TELEGRAM_API.format(token=token, method=method)
    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        logger.error(f"Telegram API {method} failed {r.status_code}: {r.text}")
        r.raise_for_status()
    result = r.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error [{method}]: {result}")
    return result


def _api_upload(token: str, method: str, data: dict, files: dict) -> dict:
    """Multipart file upload — used for sendMediaGroup with direct image bytes."""
    url = TELEGRAM_API.format(token=token, method=method)
    r = requests.post(url, data=data, files=files, timeout=60)
    if not r.ok:
        logger.error(f"Telegram API {method} failed {r.status_code}: {r.text}")
        r.raise_for_status()
    result = r.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error [{method}]: {result}")
    return result


def _build_post_body(post) -> str:
    """Assemble full post text: hook + post_text + cta_closing + hashtags."""
    if post.hook:
        body = f"{post.hook}\n\n{post.post_text}"
    else:
        body = post.post_text
    if post.cta_closing:
        body = f"{body}\n\n{post.cta_closing}"
    if post.hashtags:
        body = f"{body}\n\n{' '.join(post.hashtags)}"
    return body


def _load_pending() -> dict:
    os.makedirs(".tmp", exist_ok=True)
    if os.path.exists(PENDING_POSTS_PATH):
        with open(PENDING_POSTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_pending(data: dict) -> None:
    with open(PENDING_POSTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def send(post, config: dict, image_urls: list = None) -> str:
    """
    Send post notification to Telegram.
    - With images: sends media group (3 photos) + control message with inline buttons [1][2][3]
    - Without images: sends text message with a single "Posten" button
    Returns the tracking message_id string.
    """
    tg_cfg = config.get("telegram_notification", {})
    if not tg_cfg.get("enabled"):
        return ""

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env")

    post_body = _build_post_body(post)

    if not image_urls:
        primary = post.image_prompt if post.image_prompt and post.image_prompt.startswith("https://") else None
        image_urls = [primary] if primary else []

    # --- Send media group if images are available ---
    first_media_msg_id = None
    if image_urls:
        # Caption on first image: title only — full text is in the control message below
        media = []
        for i, url in enumerate(image_urls):
            item = {"type": "photo", "media": url}
            if i == 0:
                item["caption"] = f"*{post.post_title}*"
                item["parse_mode"] = "Markdown"
            media.append(item)

        try:
            # Download image bytes and upload directly — imgbb URLs are blocked by Telegram servers
            files = {}
            media_items = []
            for i, img_url in enumerate(image_urls):
                img_bytes = requests.get(img_url, timeout=30).content
                attach_name = f"photo{i}"
                files[attach_name] = (f"photo{i}.jpg", img_bytes, "image/jpeg")
                item = {"type": "photo", "media": f"attach://{attach_name}"}
                if i == 0:
                    item["caption"] = post.post_title
                media_items.append(item)

            result = _api_upload(token, "sendMediaGroup", {
                "chat_id": chat_id,
                "media": json.dumps(media_items),
            }, files)
            first_media_msg_id = str(result["result"][0]["message_id"])
            logger.debug(f"Telegram media group sent | first_msg_id: {first_media_msg_id}")
        except Exception as e:
            logger.warning(f"sendMediaGroup failed (sending text only): {e}")
            image_urls = []  # clear so control message falls back to single button

    # --- Send control message with inline keyboard ---
    tracking_id = first_media_msg_id or f"text_{int(datetime.now().timestamp())}"

    if image_urls and len(image_urls) >= 2:
        buttons = [[
            {"text": "1️⃣", "callback_data": f"post:{tracking_id}:0"},
            {"text": "2️⃣", "callback_data": f"post:{tracking_id}:1"},
            {"text": "3️⃣", "callback_data": f"post:{tracking_id}:2"},
        ]]
        ctrl_text = "Wähle ein Bild zum Posten auf LinkedIn:"
    else:
        buttons = [[
            {"text": "✅ Auf LinkedIn posten", "callback_data": f"post:{tracking_id}:0"}
        ]]
        ctrl_text = "Bereit zum Posten auf LinkedIn:"

    # If no media was sent, include post text in control message
    if not image_urls:
        ctrl_text = f"*{post.post_title}*\n\n{post_body[:4096]}\n\n{ctrl_text}"
    else:
        # With images: send full post text as the control message text
        ctrl_text = f"*{post.post_title}*\n\n{post_body[:4096]}\n\n{ctrl_text}"

    ctrl_result = _api(token, "sendMessage", {
        "chat_id": chat_id,
        "text": ctrl_text,
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": buttons},
    })
    ctrl_msg_id = str(ctrl_result["result"]["message_id"])

    # --- Persist to pending_posts.json ---
    pending = _load_pending()
    pending[tracking_id] = {
        "post_title": post.post_title,
        "post_body": post_body,
        "image_url": image_urls[0] if image_urls else None,
        "image_urls": image_urls,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "keyword": post.keyword,
        "ctrl_message_id": ctrl_msg_id,
    }
    _save_pending(pending)

    logger.success(f"Telegram notification sent | '{post.post_title}' | tracking_id: {tracking_id}")
    return tracking_id
