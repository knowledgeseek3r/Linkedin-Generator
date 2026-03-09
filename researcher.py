import os
from typing import List
import requests
from loguru import logger
from models import ResearchSummary

DEPTH_TO_MODEL = {
    "shallow": "sonar",
    "deep": "sonar-pro",
}


def research(keyword: str, depth: str) -> ResearchSummary:
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        logger.warning("PERPLEXITY_API_KEY not set — skipping research")
        return ResearchSummary(keyword=keyword, sources=[], summary_text=f"No research available for {keyword}.")

    model = DEPTH_TO_MODEL.get(depth, "sonar")
    logger.info(f"Researching '{keyword}' via Perplexity ({model})")

    prompt = (
        f'Research the latest developments, statistics, expert opinions, and trends '
        f'about "{keyword}" relevant for German enterprise automation and AI professionals. '
        f'Focus on: recent data points with sources, practical business implications, emerging trends. '
        f'Provide a structured English summary with all key facts and their sources.'
    )

    try:
        response = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        summary_text = data["choices"][0]["message"]["content"]
        sources: List[str] = data.get("citations", [])

        logger.info(f"Research complete for '{keyword}': {len(sources)} sources found")
        return ResearchSummary(keyword=keyword, sources=sources, summary_text=summary_text)

    except requests.RequestException as e:
        logger.warning(f"Perplexity research failed for '{keyword}': {e} — continuing without research")
        return ResearchSummary(keyword=keyword, sources=[], summary_text=f"No research available for {keyword}.")
