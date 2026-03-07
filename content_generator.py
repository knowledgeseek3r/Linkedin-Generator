import json
import os
from typing import List, Union
import anthropic
from loguru import logger
from models import ClassifiedPost, ScoredPost, ResearchSummary, GeneratedPost

CTA_INSTRUCTIONS = {
    "frage_an_community": "End the post with a direct question to the community that invites discussion.",
    "ressource_teilen": "End the post by recommending a resource or further reading on the topic.",
    "meinung_einfordern": "End the post by explicitly asking readers to share their opinion or experience.",
    "newsletter_link": "End the post with a call-to-action to subscribe to a newsletter for more insights.",
}

SYSTEM_PROMPT_TEMPLATE = """You are an experienced German-speaking LinkedIn thought leader \
specializing in enterprise automation and AI.

Writing style: {post_style}

Rules:
- Write exclusively in German
- Format for mobile: short paragraphs (2-3 sentences max), strategic line breaks, no walls of text
- Be data-driven, specific, and avoid generic statements
- Sound like a knowledgeable practitioner, not a content marketer"""


def _build_user_prompt(
    keyword: str,
    posts: List[Union[ClassifiedPost, ScoredPost]],
    research: ResearchSummary,
    config: dict,
) -> str:
    parts = [f'Create a LinkedIn post about the topic: "{keyword}"\n']

    # Voice samples injection
    voice = config.get("voice_samples", {})
    if voice.get("enabled") and voice.get("samples"):
        parts.append("Mirror this writing style exactly (tone, vocabulary, sentence structure, rhythm):")
        for i, sample in enumerate(voice["samples"], 1):
            parts.append(f"Style Example {i}:\n{sample}")
        parts.append("")

    # Inspiration posts
    top_posts = posts[:5]
    parts.append("Top-performing LinkedIn posts on this topic for inspiration (use as inspiration only, do not copy):")
    for i, post in enumerate(top_posts, 1):
        parts.append(f"Inspiration Post {i}:\n{post.text[:600]}")
    parts.append("")

    # Research insights
    parts.append(f"Research insights (incorporate relevant facts, stats, and trends):\n{research.summary_text}")
    parts.append("")

    # CTA instruction
    cta = config.get("cta", {})
    if cta.get("enabled"):
        cta_type = cta.get("type", "frage_an_community")
        parts.append(f"CTA instruction: {CTA_INSTRUCTIONS.get(cta_type, '')}")
        parts.append("")

    # Hashtag instruction
    hashtags_cfg = config.get("hashtags", {})
    if hashtags_cfg.get("enabled"):
        broad = hashtags_cfg.get("broad_count", 2)
        niche = hashtags_cfg.get("niche_count", 3)
        parts.append(f"Include exactly {broad} broad hashtags (e.g. #AI #Automatisierung) and {niche} niche hashtags (e.g. #AgenticAI). Add them in the hashtags field.")
        parts.append("")

    # Output schema (conditional)
    hook_field = ""
    if config.get("generate_hook"):
        hook_field = (
            '\n  "hook": "Standalone first 210 characters. Must create a curiosity gap or bold statement '
            'before the See More cutoff. Write it independently — do NOT truncate post_text.",'
        )

    cta_field = ""
    if cta.get("enabled"):
        cta_field = '\n  "cta_closing": "Explicit CTA sentence matching the configured type",'

    hashtags_field = ""
    if hashtags_cfg.get("enabled"):
        hashtags_field = '\n  "hashtags": ["#Broad1", "#Broad2", "#Niche1", "#Niche2", "#Niche3"],'

    parts.append(f"""Return ONLY valid JSON — no text outside the JSON object:
{{
  "post_title": "catchy German headline, max 10 words",
  "post_text": "full German LinkedIn post, 150-300 words, mobile-optimized formatting",{hook_field}
  "image_prompt": "detailed English image generation prompt in DALL-E/Midjourney style, describing a visual that matches the post theme",{cta_field}{hashtags_field}
}}""")

    return "\n".join(parts)


def _parse_response(text: str, config: dict, keyword: str) -> GeneratedPost:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip().rstrip("`").strip()

    data = json.loads(text)
    return GeneratedPost(
        keyword=keyword,
        post_title=data["post_title"],
        post_text=data["post_text"],
        image_prompt=data["image_prompt"],
        hook=data.get("hook") if config.get("generate_hook") else None,
        cta_closing=data.get("cta_closing") if config.get("cta", {}).get("enabled") else None,
        hashtags=data.get("hashtags") if config.get("hashtags", {}).get("enabled") else None,
    )


def generate(
    keyword: str,
    posts: List[Union[ClassifiedPost, ScoredPost]],
    research: ResearchSummary,
    config: dict,
) -> GeneratedPost:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(post_style=config.get("post_style", "concise, data-driven"))
    user_prompt = _build_user_prompt(keyword, posts, research, config)

    logger.info(f"Generating post for '{keyword}'")

    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text
            post = _parse_response(raw, config, keyword)
            logger.success(f"Generated post: '{post.post_title}'")
            return post

        except json.JSONDecodeError as e:
            if attempt == 0:
                logger.warning(f"JSON parse error (attempt 1): {e} — retrying with correction prompt")
                # Add correction instruction and retry
                user_prompt = user_prompt + "\n\nIMPORTANT: Your previous response was not valid JSON. Return ONLY the raw JSON object, nothing else."
                continue
            raise RuntimeError(f"Content generation failed for '{keyword}' after retry: {e}") from e
