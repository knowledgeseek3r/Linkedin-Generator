import os
from typing import List
import anthropic
from loguru import logger
from models import ResearchSummary

DEPTH_TO_MAX_USES = {
    "shallow": 3,
    "deep": 10,
}


def _extract_results(response) -> tuple[str, List[str]]:
    text_parts = []
    sources = []

    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif hasattr(block, "type") and block.type == "tool_result":
            # web_search_result blocks contain source URLs
            if hasattr(block, "content"):
                for item in block.content:
                    if hasattr(item, "url"):
                        sources.append(item.url)

    # Also try to extract URLs cited in the text (fallback)
    if not sources:
        import re
        all_text = " ".join(text_parts)
        sources = re.findall(r"https?://[^\s\)\]\"']+", all_text)[:10]

    return "\n\n".join(text_parts), list(dict.fromkeys(sources))  # deduplicate


def research(keyword: str, depth: str) -> ResearchSummary:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=120.0)
    max_uses = DEPTH_TO_MAX_USES.get(depth, 3)

    prompt = f"""Research the latest developments, trends, expert opinions, statistics, \
and notable insights about "{keyword}" for German enterprise automation and AI professionals.

Focus on:
- Developments from the last 30 days
- Key statistics or data points with sources
- Expert quotes or opinions
- Practical business implications
- Emerging trends

Provide a structured summary in English. List all sources you consulted."""

    logger.info(f"Researching '{keyword}' (depth={depth}, max_uses={max_uses})")

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            tools=[{
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": max_uses,
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        summary_text, sources = _extract_results(response)
        logger.info(f"Research complete for '{keyword}': {len(sources)} sources found")
        return ResearchSummary(keyword=keyword, sources=sources, summary_text=summary_text)

    except Exception as e:
        logger.warning(f"Web research failed for '{keyword}': {e} — continuing without research")
        return ResearchSummary(
            keyword=keyword,
            sources=[],
            summary_text=f"No research available for {keyword} due to API error.",
        )
