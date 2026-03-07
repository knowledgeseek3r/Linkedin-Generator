import json
import os
from typing import List, Optional
import anthropic
from loguru import logger
from models import ScrapedPost, ClassifiedPost

MIN_KEEP = 3


def _build_prompt(posts: List[ScrapedPost], keyword: str) -> str:
    numbered = "\n\n".join(
        f"{i+1}. {p.text[:800]}" for i, p in enumerate(posts)
    )
    return f"""You are a LinkedIn content quality classifier. Classify each post for relevance to the keyword "{keyword}".

KEEP posts that are:
- Educational or knowledge-sharing (how things work, frameworks, use cases)
- Industry news or trend analysis with factual context
- Technical insights, research summaries
- Grounded opinions about industry topics

DISCARD posts that are:
- Personal milestones (certifications, promotions, anniversaries)
- Company self-promotion (hiring, product launches, partnerships)
- Pure motivational content without substantive knowledge
- Event announcements without educational value
- Engagement bait ("Comment YES if you agree")

Posts to classify:

{numbered}

Return ONLY a JSON array — no text outside the JSON:
[
  {{"post_index": 1, "type": "educational", "keep": true, "reason": "one line explanation"}},
  {{"post_index": 2, "type": "personal", "keep": false, "reason": "one line explanation"}}
]"""


def _parse_response(text: str, posts: List[ScrapedPost]) -> List[ClassifiedPost]:
    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip().rstrip("`").strip()

    results = json.loads(text)
    classified = []
    for r in results:
        idx = r["post_index"] - 1
        if idx < 0 or idx >= len(posts):
            continue
        post = posts[idx]
        classified.append(ClassifiedPost(
            **post.model_dump(),
            post_index=r["post_index"],
            type=r.get("type", "other"),
            keep=bool(r.get("keep", False)),
            reason=r.get("reason", ""),
        ))
    return classified


def classify(posts: List[ScrapedPost], keyword: str) -> Optional[List[ClassifiedPost]]:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    for attempt in range(2):
        try:
            prompt = _build_prompt(posts, keyword)
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
            classified = _parse_response(raw, posts)
            break
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            if attempt == 0:
                logger.warning(f"Classifier JSON parse error (attempt 1): {e} — retrying")
                continue
            logger.error(f"Classifier failed after retry: {e}")
            return None
        except Exception as e:
            if attempt == 0:
                logger.warning(f"Classifier API error (attempt 1): {e} — retrying")
                continue
            logger.error(f"Classifier API failed after retry: {e}")
            return None

    kept = [p for p in classified if p.keep]
    discarded = [p for p in classified if not p.keep]

    for p in discarded:
        logger.info(f"Discarded [{p.type}]: {p.url} | reason: {p.reason}")

    logger.info(f"Classifier: {len(kept)} kept, {len(discarded)} discarded for '{keyword}'")

    if len(kept) < MIN_KEEP:
        logger.warning(f"Only {len(kept)} posts passed classifier for '{keyword}' (min {MIN_KEEP})")
        return None

    return kept
