"""
email_notifier.py
Sends an HTML email for each generated LinkedIn post.
Stores the Message-ID in .tmp/pending_posts.json so reply_checker.py
can match incoming replies and trigger LinkedIn posting.
"""

import os
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid, formatdate
from datetime import datetime, timezone

from loguru import logger

PENDING_POSTS_PATH = ".tmp/pending_posts.json"


# ---------------------------------------------------------------------------
# Post body assembly — mirrors sheets_client._build_row() exactly
# ---------------------------------------------------------------------------

def _build_post_body(post) -> str:
    """Assemble column-B text: hook + post_text + cta_closing + hashtags."""
    if post.hook:
        body = f"{post.hook}\n\n{post.post_text}"
    else:
        body = post.post_text
    if post.cta_closing:
        body = f"{body}\n\n{post.cta_closing}"
    if post.hashtags:
        body = f"{body}\n\n{' '.join(post.hashtags)}"
    return body


# ---------------------------------------------------------------------------
# HTML email builder
# ---------------------------------------------------------------------------

def _build_html(post_title: str, post_body: str, image_urls: list,
                reply_trigger: str) -> str:
    """Build HTML email. If multiple images, shows selection grid with numbered labels."""
    html_body = post_body.replace("\n", "<br>")

    if len(image_urls) > 1:
        cells = "".join(
            f'<td align="center" style="padding:8px;">'
            f'<img src="{url}" alt="Bild {i+1}" width="180" style="border-radius:6px;display:block;">'
            f'<p style="margin:6px 0 0 0;font-size:18px;font-weight:bold;color:#0077b5;">{i+1}</p>'
            f'</td>'
            for i, url in enumerate(image_urls)
        )
        image_html = (
            f'<div style="margin-top:24px;">'
            f'<p style="font-size:14px;color:#444;margin:0 0 12px 0;">'
            f'<strong>Wähle ein Bild:</strong></p>'
            f'<table><tr>{cells}</tr></table>'
            f'</div>'
        )
        cta_text = f'Antworte mit <strong style="color:#0077b5;">1</strong>, <strong style="color:#0077b5;">2</strong> oder <strong style="color:#0077b5;">3</strong> um das gewählte Bild auf LinkedIn zu veröffentlichen.'
    elif image_urls:
        image_html = (
            f'<div style="margin-top:24px;">'
            f'<img src="{image_urls[0]}" alt="Beitragsbild" '
            f'style="max-width:600px;width:100%;border-radius:6px;">'
            f'</div>'
        )
        cta_text = f'Antworte mit <strong style="color:#0077b5;">{reply_trigger}</strong> auf diese E-Mail, um diesen Beitrag automatisch auf LinkedIn zu veröffentlichen.'
    else:
        image_html = ""
        cta_text = f'Antworte mit <strong style="color:#0077b5;">{reply_trigger}</strong> auf diese E-Mail, um diesen Beitrag automatisch auf LinkedIn zu veröffentlichen.'

    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;padding:24px;color:#1a1a1a;">
  <div style="border-left:4px solid #0077b5;padding-left:16px;margin-bottom:20px;">
    <h2 style="color:#0077b5;margin:0 0 4px 0;">LinkedIn Beitrag</h2>
    <p style="margin:0;color:#666;font-size:14px;">{post_title}</p>
  </div>
  <div style="white-space:pre-wrap;line-height:1.7;font-size:15px;background:#f9f9f9;
              padding:16px;border-radius:6px;">
{html_body}
  </div>
  {image_html}
  <hr style="margin:28px 0;border:none;border-top:1px solid #e0e0e0;">
  <p style="color:#888;font-size:13px;margin:0;">{cta_text}</p>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Pending posts persistence
# ---------------------------------------------------------------------------

def _load_pending() -> dict:
    os.makedirs(".tmp", exist_ok=True)
    if os.path.exists(PENDING_POSTS_PATH):
        with open(PENDING_POSTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_pending(data: dict) -> None:
    with open(PENDING_POSTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send(post, config: dict, image_urls: list = None) -> str:
    """
    Send an HTML email for the given GeneratedPost.
    image_urls: list of image URLs (3 for selection, 1 for single, or [] for none).
    Stores post data in .tmp/pending_posts.json keyed by Message-ID.
    Returns the Message-ID string.
    """
    email_cfg = config["email_notification"]
    smtp_host = email_cfg["smtp_host"]
    smtp_port = int(email_cfg["smtp_port"])
    sender = email_cfg["sender_email"]
    password = email_cfg["sender_password"]
    recipient = email_cfg["recipient_email"]
    reply_trigger = email_cfg["reply_trigger"]

    post_body = _build_post_body(post)

    # Resolve image URLs: prefer passed image_urls, fallback to post.image_prompt if it's a URL
    if not image_urls:
        primary = post.image_prompt if post.image_prompt and post.image_prompt.startswith("https://") else None
        image_urls = [primary] if primary else []

    # Build message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[LinkedIn] {post.post_title}"
    msg["From"] = sender
    msg["To"] = recipient
    msg["Date"] = formatdate(localtime=True)

    domain = sender.split("@")[1] if "@" in sender else "mail.com"
    message_id = make_msgid(domain=domain)
    msg["Message-ID"] = message_id

    html_content = _build_html(post.post_title, post_body, image_urls, reply_trigger)
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    logger.info(f"Sending email: '{post.post_title}' → {recipient}")
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(sender, password)
            server.sendmail(sender, [recipient], msg.as_string())
        logger.success(f"Email sent | Message-ID: {message_id}")
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "SMTP authentication failed — check EMAIL_USER and EMAIL_PASSWORD in .env. "
            "Gmail requires an App Password (not your account password)."
        )
        raise
    except smtplib.SMTPException as exc:
        logger.error(f"SMTP error sending email for '{post.post_title}': {exc}")
        raise

    # Persist to pending_posts.json for reply_checker.py
    pending = _load_pending()
    pending[message_id] = {
        "post_title": post.post_title,
        "post_body": post_body,
        "image_url": image_urls[0] if image_urls else None,   # primary (for Sheets/backward compat)
        "image_urls": image_urls,                              # all options for reply selection
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "keyword": post.keyword,
    }
    _save_pending(pending)
    logger.debug(f"Saved to pending_posts.json: {message_id} ({len(image_urls)} image(s))")

    return message_id
