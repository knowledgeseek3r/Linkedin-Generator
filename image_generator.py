import base64
import os

import requests
from loguru import logger
from openai import OpenAI

_NO_TEXT_SUFFIX = (
    " The image must contain absolutely no text, letters, words, numbers, labels, "
    "captions, signs, watermarks, or typography of any kind."
)


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


def _ideogram_generate(prompt: str) -> bytes:
    api_key = os.getenv("IDEOGRAM_API_KEY")
    if not api_key:
        raise EnvironmentError("IDEOGRAM_API_KEY is not set in .env")
    logger.debug(f"Ideogram V3 prompt: {prompt[:120]}...")
    response = requests.post(
        "https://api.ideogram.ai/v1/ideogram-v3/generate",
        headers={"Api-Key": api_key, "Content-Type": "application/json"},
        json={
            "prompt": prompt,
            "aspect_ratio": "1x1",
            "rendering_speed": "QUALITY",
        },
        timeout=120,
    )
    response.raise_for_status()
    image_url = response.json()["data"][0]["url"]
    logger.debug(f"Ideogram image URL: {image_url}")
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


def generate_multiple(prompt: str, keyword: str, config: dict = None, n: int = 3) -> list:
    """Generate n images via configured provider, upload all to imgbb. Returns list of URLs."""
    provider = (config or {}).get("image_generation", {}).get("provider", "dalle3")
    logger.info(f"Generating {n} images for '{keyword}' via {provider}")
    urls = []
    for i in range(n):
        logger.info(f"Generating image {i + 1}/{n} for '{keyword}'")
        try:
            if provider == "ideogram":
                image_bytes = _ideogram_generate(prompt)
            else:
                image_bytes = _dalle_generate(prompt + _NO_TEXT_SUFFIX)
            urls.append(_upload_to_imgbb(image_bytes))
        except Exception as e:
            logger.warning(f"Image {i + 1}/{n} failed: {e}")
    if not urls:
        raise RuntimeError(f"All {n} image generations failed for '{keyword}'")
    return urls


def generate_and_upload(prompt: str, keyword: str, config: dict = None) -> str:
    """Generate a single image. Convenience wrapper around generate_multiple."""
    return generate_multiple(prompt, keyword, config, n=1)[0]
