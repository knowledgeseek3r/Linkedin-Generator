import json
import os
import time
from loguru import logger
import gspread
from models import GeneratedPost

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
    image_col = "Beitragsbild" if config.get("image_generation", {}).get("enabled") else "Image Prompt"
    return ["Thema / Titel", "Beitragstext", image_col]


def _build_row(post: GeneratedPost, config: dict) -> list:
    # Hook as first paragraph
    if post.hook:
        body = f"{post.hook}\n\n{post.post_text}"
    else:
        body = post.post_text

    # CTA appended after body
    if post.cta_closing:
        body = f"{body}\n\n{post.cta_closing}"

    # Hashtags appended at the end
    if post.hashtags:
        body = f"{body}\n\n{' '.join(post.hashtags)}"

    # image_prompt holds the Drive URL when image_generation is active, otherwise the raw prompt text
    if post.image_prompt.startswith("https://"):
        image_cell = f'=IMAGE("{post.image_prompt}")'
    else:
        image_cell = post.image_prompt
    return [post.post_title, body, image_cell]


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
