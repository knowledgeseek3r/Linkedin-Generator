import base64
import os

import requests
from loguru import logger
from openai import OpenAI


def _dalle_generate(prompt: str) -> bytes:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        quality="standard",
        n=1,
    )
    image_url = response.data[0].url
    logger.debug(f"DALL-E 3 image URL (temporary): {image_url}")
    img_response = requests.get(image_url, timeout=60)
    img_response.raise_for_status()
    return img_response.content


def _upload_to_imgbb(image_bytes: bytes) -> str:
    api_key = os.getenv("IMGBB_API_KEY")
    if not api_key:
        raise EnvironmentError("IMGBB_API_KEY is not set in .env")
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    response = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": api_key, "image": b64},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("success"):
        raise RuntimeError(f"imgbb upload failed: {data}")
    url = data["data"]["display_url"]
    logger.debug(f"Uploaded to imgbb: {url}")
    return url


_NO_TEXT_SUFFIX = (
    " The image must contain absolutely no text, letters, words, numbers, labels, "
    "captions, signs, watermarks, or typography of any kind."
)


def generate_and_upload(prompt: str, keyword: str) -> str:
    """Generate image with DALL-E 3, upload to imgbb, return permanent URL."""
    logger.info(f"Generating image for '{keyword}'")
    image_bytes = _dalle_generate(prompt + _NO_TEXT_SUFFIX)
    url = _upload_to_imgbb(image_bytes)
    logger.success(f"Image ready: {url}")
    return url
