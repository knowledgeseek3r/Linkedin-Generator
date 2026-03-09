"""
reply_checker.py
Standalone script: polls Gmail IMAP for replies to pending post emails.

When a reply is found whose first line contains the trigger word (e.g. "Post"),
the corresponding LinkedIn post is published and removed from pending_posts.json.

Usage:
  python reply_checker.py           # polling loop (60s interval, max 60 iterations)
  python reply_checker.py --once    # single check and exit

Configuration is read from config.yaml.
Credentials come from .env via config_loader.
"""

import sys
import os
import json
import time
import imaplib
import email
from datetime import datetime, timedelta
from email.header import decode_header

from loguru import logger

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_loader import load_config
import linkedin_poster

PENDING_POSTS_PATH = ".tmp/pending_posts.json"
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
POLL_INTERVAL_SECONDS = 60
MAX_ITERATIONS = 60  # safety cap when running in loop mode


# ---------------------------------------------------------------------------
# Pending posts helpers
# ---------------------------------------------------------------------------

def _load_pending() -> dict:
    if not os.path.exists(PENDING_POSTS_PATH):
        return {}
    with open(PENDING_POSTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_pending(data: dict) -> None:
    with open(PENDING_POSTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Email parsing helpers
# ---------------------------------------------------------------------------

def _decode_header_value(raw: str) -> str:
    """Safely decode RFC 2047 encoded email header."""
    parts = decode_header(raw or "")
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def _get_plain_body(msg: email.message.Message) -> str:
    """Extract plain-text body from an email.message.Message object."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
    else:
        charset = msg.get_content_charset() or "utf-8"
        return msg.get_payload(decode=True).decode(charset, errors="replace")
    return ""


def _first_non_empty_line(text: str) -> str:
    """Return the first non-empty, non-whitespace line of a string."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


# ---------------------------------------------------------------------------
# Core check logic
# ---------------------------------------------------------------------------

def check_replies(config: dict) -> int:
    """
    Connect to Gmail IMAP, find unread replies matching pending Message-IDs,
    and post to LinkedIn for each approved reply.

    Returns the number of posts published in this run.
    """
    email_cfg = config.get("email_notification", {})
    li_cfg = config.get("linkedin_posting", {})

    if not email_cfg.get("enabled"):
        logger.warning("email_notification is disabled in config — nothing to check")
        return 0

    if not li_cfg.get("enabled"):
        logger.warning("linkedin_posting is disabled — replies detected but no action taken")
        return 0

    pending = _load_pending()
    if not pending:
        logger.info("No pending posts in .tmp/pending_posts.json — nothing to check")
        return 0

    sender_email = email_cfg["sender_email"]
    password = email_cfg["sender_password"]
    reply_trigger = email_cfg["reply_trigger"].strip().lower()

    published_count = 0

    logger.info(f"Connecting to IMAP: {IMAP_HOST}")
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(sender_email, password)
    except imaplib.IMAP4.error as exc:
        logger.error(
            f"IMAP login failed: {exc} — "
            "check EMAIL_USER and EMAIL_PASSWORD in .env (Gmail requires App Password)"
        )
        raise

    try:
        mail.select("INBOX")
        since_date = (datetime.now() - timedelta(days=10)).strftime("%d-%b-%Y")
        status, data = mail.search(None, f'(UNSEEN SINCE "{since_date}")')
        if status != "OK":
            logger.warning("IMAP SEARCH returned non-OK status")
            return 0

        email_ids = data[0].split()
        logger.info(f"Found {len(email_ids)} unread email(s) in the last 10 days to check")

        pending_ids = set(pending.keys())

        for eid in email_ids:
            status, msg_data = mail.fetch(eid, "(RFC822)")
            if status != "OK":
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            in_reply_to = (msg.get("In-Reply-To") or "").strip()
            references = (msg.get("References") or "").strip()

            # Match against pending Message-IDs
            matched_id = None
            for pending_msg_id in pending_ids:
                clean_pending = pending_msg_id.strip("<>")
                clean_reply = in_reply_to.strip("<>")
                if clean_pending == clean_reply or pending_msg_id in references:
                    matched_id = pending_msg_id
                    break

            if not matched_id:
                # Not a reply to any of our emails — leave unread
                continue

            post_data = pending[matched_id]
            subject = _decode_header_value(msg.get("Subject", ""))
            logger.info(f"Reply found for '{post_data['post_title']}' (subject: {subject!r})")

            # Check first non-empty line — accepts "1"/"2"/"3" (image selection) or trigger word
            body = _get_plain_body(msg)
            first_line = _first_non_empty_line(body).strip()
            first_line_lower = first_line.lower()

            available_images = post_data.get("image_urls", [])
            selected_image = post_data.get("image_url")  # default = first image

            if available_images and first_line in ("1", "2", "3"):
                idx = int(first_line) - 1
                if idx < len(available_images):
                    selected_image = available_images[idx]
                    logger.info(f"Image {first_line} selected: {selected_image}")
                else:
                    selected_image = available_images[0]
                    logger.warning(f"Image index {first_line} out of range — using image 1")
            elif reply_trigger.lower() not in first_line_lower:
                logger.info(
                    f"No valid selection in first line: {first_line[:60]!r} — skipping"
                )
                mail.store(eid, "+FLAGS", "\\Seen")
                continue

            # Trigger matched — post to LinkedIn
            logger.info(f"Trigger matched for '{post_data['post_title']}' — posting to LinkedIn")
            try:
                linkedin_poster.post_to_linkedin(
                    post_body=post_data["post_body"],
                    image_url=selected_image,
                    config=config,
                )
                # Success — remove from pending, mark email as read
                del pending[matched_id]
                _save_pending(pending)
                pending_ids.discard(matched_id)
                published_count += 1
                mail.store(eid, "+FLAGS", "\\Seen")
                logger.success(f"Published to LinkedIn: '{post_data['post_title']}'")

            except PermissionError as exc:
                # Token expired — keep email unread so user can retry after refreshing token
                logger.error(str(exc))
                logger.error(
                    "ACTION REQUIRED: Update LINKEDIN_ACCESS_TOKEN in .env, "
                    "then re-run reply_checker.py"
                )
                # Do NOT mark as seen — preserve for retry

            except Exception as exc:
                logger.error(f"LinkedIn posting failed for '{post_data['post_title']}': {exc}")
                # Mark as seen to avoid infinite retry on hard failures
                mail.store(eid, "+FLAGS", "\\Seen")

    finally:
        try:
            mail.logout()
        except Exception:
            pass

    logger.info(f"Check complete — {published_count} post(s) published this run")
    return published_count


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_loop(config: dict) -> None:
    """Poll IMAP on a fixed interval. Ctrl+C to stop."""
    logger.info(
        f"Reply checker started — polling every {POLL_INTERVAL_SECONDS}s "
        f"(max {MAX_ITERATIONS} iterations). Press Ctrl+C to stop."
    )
    for i in range(MAX_ITERATIONS):
        try:
            check_replies(config)
        except Exception as exc:
            logger.error(f"Reply check iteration {i + 1} failed: {exc}")
        time.sleep(POLL_INTERVAL_SECONDS)
    logger.info("Max iterations reached — exiting")


if __name__ == "__main__":
    run_once = "--once" in sys.argv
    cfg = load_config("config.yaml")

    if run_once:
        logger.info("Running single check (--once mode)")
        check_replies(cfg)
    else:
        run_loop(cfg)
