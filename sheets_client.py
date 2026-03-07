import json
import os
import time
from loguru import logger
import gspread
from models import GeneratedPost

POSTING_TIME = "Di–Do 08:00–10:00"
MAX_RETRIES = 3


def _get_client() -> gspread.Client:
    creds_path = os.getenv("GSPREAD_CREDENTIALS_PATH", "credentials.json")
    if not os.path.exists(creds_path):
        raise FileNotFoundError(
            f"Google credentials file not found at '{creds_path}'. "
            "Create a service account, download the JSON, and save it as credentials.json in the project root."
        )
    with open(creds_path, "r") as f:
        creds_dict = json.load(f)
    return gspread.service_account_from_dict(creds_dict)


def _get_headers(config: dict) -> list:
    headers = ["Thema / Titel", "Beitragstext", "Image Prompt", "Posting-Zeitpunkt"]
    if config.get("generate_hook"):
        headers.append("Hook")
    if config.get("hashtags", {}).get("enabled"):
        headers.append("Hashtags")
    return headers


def _build_row(post: GeneratedPost, config: dict) -> list:
    body = post.post_text
    if post.cta_closing:
        body = f"{body}\n\n{post.cta_closing}"

    row = [post.post_title, body, post.image_prompt, POSTING_TIME]

    if config.get("generate_hook"):
        row.append(post.hook or "")

    if config.get("hashtags", {}).get("enabled"):
        row.append(" ".join(post.hashtags) if post.hashtags else "")

    return row


def _append_with_retry(ws: gspread.Worksheet, row: list) -> None:
    for attempt in range(MAX_RETRIES):
        try:
            ws.append_row(row, value_input_option="USER_ENTERED")
            return
        except gspread.exceptions.APIError as e:
            wait = 2 ** attempt
            logger.warning(f"Sheets write error (attempt {attempt + 1}): {e} — retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError("Google Sheets write failed after all retries")


def write(post: GeneratedPost, config: dict) -> None:
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise EnvironmentError("GOOGLE_SHEET_ID is not set in .env")

    gc = _get_client()
    sh = gc.open_by_key(sheet_id)
    ws = sh.sheet1

    # Add header row if sheet is empty
    existing = ws.get_all_values()
    if not existing:
        headers = _get_headers(config)
        ws.append_row(headers, value_input_option="USER_ENTERED")
        logger.info("Added header row to empty sheet")
        time.sleep(1)  # avoid hitting rate limit immediately after header write

    row = _build_row(post, config)
    _append_with_retry(ws, row)
    logger.success(f"Written to Sheets: '{post.post_title}' (keyword: {post.keyword})")
