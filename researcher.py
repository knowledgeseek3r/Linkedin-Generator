import concurrent.futures
import os
from typing import List
import anthropic
from loguru import logger
from models import ResearchSummary

DEPTH_TO_MAX_USES = {
    "shallow": 3,
    "deep": 10,
}
RESEARCH_TIMEOUT_SECONDS = 90


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


def _call_research_api(keyword: str, max_uses: int) -> ResearchSummary:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = f"""Research the latest developments, trends, expert opinions, statistics, \
and notable insights about "{keyword}" for German enterprise automation and AI professionals.

Focus on:
- Developments from the last 30 days
- Key statistics or data points with sources
- Expert quotes or opinions
- Practical business implications
- Emerging trends

Provide a structured summary in English. List all sources you consulted."""

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
    return ResearchSummary(keyword=keyword, sources=sources, summary_text=summary_text)


def research(keyword: str, depth: str) -> ResearchSummary:
    max_uses = DEPTH_TO_MAX_USES.get(depth, 3)
    logger.info(f"Researching '{keyword}' (depth={depth}, max_uses={max_uses})")

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_call_research_api, keyword, max_uses)
    try:
        result = future.result(timeout=RESEARCH_TIMEOUT_SECONDS)
        logger.info(f"Research complete for '{keyword}': {len(result.sources)} sources found")
        return result

    except concurrent.futures.TimeoutError:
        future.cancel()
        executor.shutdown(wait=False)
        logger.warning(f"Web research timed out after {RESEARCH_TIMEOUT_SECONDS}s for '{keyword}' — continuing without research")
        return ResearchSummary(
            keyword=keyword,
            sources=[],
            summary_text=f"No research available for {keyword} due to timeout.",
        )
    except Exception as e:
        executor.shutdown(wait=False)
        logger.warning(f"Web research failed for '{keyword}': {e} — continuing without research")
        return ResearchSummary(
            keyword=keyword,
            sources=[],
            summary_text=f"No research available for {keyword} due to API error.",
        )
    else:
        executor.shutdown(wait=False)
