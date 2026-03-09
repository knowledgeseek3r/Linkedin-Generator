import json
import os
from typing import List, Union
import anthropic
from loguru import logger
from models import ClassifiedPost, ScoredPost, ResearchSummary, GeneratedPost

VARIATION_ANGLES = [
    "Focus on a concrete business case or ROI example — use numbers, percentages, or a before/after scenario.",
    "Take a contrarian or myth-busting angle — challenge a common assumption or hype around this topic.",
    "Share a practical step-by-step framework or actionable checklist practitioners can apply immediately.",
    "Highlight a recent trend, prediction, or shift — what is changing and what should professionals watch?",
    "Explain a complex concept with a clear analogy or comparison — make the abstract tangible for a non-technical audience.",
]

CTA_INSTRUCTIONS = {
    "frage_an_community": (
        "Write a direct question to the community that invites discussion. "
        "Do NOT include this in post_text — put it ONLY in the cta_closing field.\n"
        "FORBIDDEN words in the CTA: Pilot, pilotieren, skaliert, Skalierung, Prozessintegration, isoliert.\n"
        "The question must be genuinely formulated for this specific post — not a generic template with filled-in placeholders. "
        "It should feel like it was written by someone who just read the post and is curious about this exact topic.\n"
        "Vary the style each time. These are examples of the TYPE of question to ask — not templates to copy:\n"
        "- Challenge a common assumption specific to this post's claim\n"
        "- Ask about a concrete decision or turning point in the reader's organization\n"
        "- Ask what surprised readers most about a specific finding in the post\n"
        "- Ask about a real obstacle readers face in this exact context\n"
        "- Ask how different roles (e.g. IT vs. management) experience this differently\n"
        "- Ask what readers would do differently knowing what the post revealed\n"
        "The question must reference the concrete topic of THIS post, not the topic in general."
    ),
    "ressource_teilen": "Recommend a resource or further reading on the topic. Do NOT include this in post_text — put it ONLY in the cta_closing field.",
    "meinung_einfordern": "Explicitly ask readers to share their opinion or experience. Do NOT include this in post_text — put it ONLY in the cta_closing field.",
    "newsletter_link": "Write a call-to-action to subscribe to a newsletter for more insights. Do NOT include this in post_text — put it ONLY in the cta_closing field.",
}

SYSTEM_PROMPT_TEMPLATE = """You are an experienced German-speaking LinkedIn thought leader \
specializing in enterprise automation and AI.

Writing style: {post_style}

Rules:
- Write exclusively in German
- Format for mobile: short paragraphs (2-3 sentences max), strategic line breaks, no walls of text
- Be data-driven, specific, and avoid generic statements
- Sound like a knowledgeable practitioner, not a content marketer
- NEVER write in first person. Do NOT use "ich sehe", "ich begleite", "in meiner Erfahrung", "ich empfehle", "bei meinen Kunden", "ich arbeite mit", or any similar personal coaching language.
- Write as an industry analyst or in knowledge-article style — factual, use neutral framing ("Unternehmen", "Führungskräfte", "man sieht", "die Praxis zeigt")."""

OPTIMIZE_PROMPT = """Du bist ein LinkedIn Content Experte. Optimiere den folgenden Post basierend auf diesen Kriterien:

1. **Hook** – Erste Zeile muss zum Weiterlesen zwingen (Zahl, These, oder Story-Einstieg)
2. **Struktur** – Kurze Absätze, Weißraum, Aufbau: Hook → Kontext → Mehrwert → CTA
3. **Mehrwert** – Konkretes Takeaway das der Leser mitnehmen kann
4. **Ton** – Menschlich, authentisch, keine Marketing-Sprache
5. **CTA** – Frage am Ende die Kommentare provoziert
6. **Algorithmus** – Keine externen Links, 3-5 relevante Hashtags, ~1300 Zeichen
7. **Quellen** – Alle Quellenangaben in Klammern (z.B. "(Deloitte, 2025)") MÜSSEN erhalten bleiben. Niemals eine Quellenangabe entfernen oder verändern.
8. **Kein "Ich"** – Nie erste Person verwenden. Kein "ich", "meine", "bei mir".

Wichtig: Behalte den Titel als allerersten Satz bei, gefolgt von einem Leerzeichen (Absatz).

Gib NUR den optimierten Post zurück."""


def _build_user_prompt(
    keyword: str,
    posts: List[Union[ClassifiedPost, ScoredPost]],
    research: ResearchSummary,
    config: dict,
    variation_index: int = 0,
) -> str:
    angle = VARIATION_ANGLES[variation_index % len(VARIATION_ANGLES)]
    parts = [
        f'Create a LinkedIn post about the topic: "{keyword}"\n',
        f'POST ANGLE (mandatory — your post MUST follow this specific angle): {angle}\n',
    ]

    # Voice samples injection
    voice = config.get("voice_samples", {})
    if voice.get("enabled") and voice.get("samples"):
        parts.append("Mirror this writing style exactly (tone, vocabulary, sentence structure, rhythm):")
        for i, sample in enumerate(voice["samples"], 1):
            parts.append(f"Style Example {i}:\n{sample}")
        parts.append("")

    # Content quality rules (optional, from config)
    content_rules = config.get("content_rules", {})
    if content_rules.get("verified_case_studies_only"):
        parts.append("""RULE — Fallstudien & Quellen:
Erwähne NUR dann eine konkrete Fallstudie oder Studie wenn sie von einem bekannten Analyst-Haus stammt (Gartner, Deloitte, McKinsey, Forrester, IDC).
Wenn der Input-Text eine Fallstudie eines unbekannten/privaten Unternehmens enthält: Erwähne KEINE spezifische Fallstudie. Verallgemeinere stattdessen: "In der Praxis zeigt sich:", "Viele Unternehmen kämpfen mit...", "Typischerweise gilt:"
NIEMALS eine Fallstudie erfinden oder implizieren die nicht öffentlich bekannt ist.
VERBOTEN: Die Phrase "Ein Beispiel aus der Praxis:" darf NUR verwendet werden wenn DIREKT danach ein konkreter, namentlich genannter Unternehmensname UND eine Quellenangabe folgen (z.B. "Ein Beispiel aus der Praxis: Siemens reduzierte... (Forrester, 2024)"). Ohne beides: verwende "In der Praxis zeigt sich:" oder "Typischerweise gilt:" stattdessen.
""")

    if content_rules.get("cite_statistics"):
        parts.append("""RULE — Statistiken & Zahlen:
Wenn du eine Statistik, Prozentzahl oder Zahlenklaim im Post verwendest, MUSST du die Quelle direkt danach in Klammern angeben.
Beispiel: "Nur 11 % der Unternehmen nutzen Agentic AI aktiv im Produktivbetrieb. (Deloitte Emerging Technology Trends, 2025)"
Verwende NUR Statistiken mit bekannter, verifizierbarer Quelle. Erfinde KEINE Statistiken.
""")

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
            '\n  "hook": "Standalone opening line, max 210 characters. Must create a curiosity gap or bold statement. '
            'Do NOT include this in post_text — put it ONLY in the hook field. post_text starts AFTER the hook.",'
        )

    cta_field = ""
    if cta.get("enabled"):
        cta_field = '\n  "cta_closing": "Explicit CTA sentence matching the configured type",'

    hashtags_field = ""
    if hashtags_cfg.get("enabled"):
        hashtags_field = '\n  "hashtags": ["#Broad1", "#Broad2", "#Niche1", "#Niche2", "#Niche3"],'

    parts.append(f"""Return ONLY valid JSON — no text outside the JSON object.
CRITICAL JSON RULES: Never use double quotes inside string values. Use dashes (–) or rephrase instead of quoting words.

{{
  "post_title": "catchy German headline, max 10 words",
  "post_text": "full German LinkedIn post, 150-300 words, mobile-optimized formatting",{hook_field}
  "image_prompt": "Ideogram V3 image prompt. Create ONE powerful visual metaphor that represents the single core insight of this post.\n\nVISUAL RULES:\n- Single focal point only — no collage, no split scenes\n- Cinematic or minimalist professional style\n- Photorealistic or clean digital art\n- Deep, purposeful color palette (no random bright colors)\n\nTEXT RULES (critical):\n- Maximum 1 word total — only if absolutely essential to the concept\n- The word must be: short (max 10 chars), common English, correctly spelled\n- Forbidden: repeating any word from the post title or subject\n- Forbidden: abstract compound words, technical jargon, invented terms\n- If unsure about spelling → use NO text at all\n- Default choice: NO TEXT — let the visual speak alone\n\nCOMPOSITION:\n- The image must tell ONE story at a glance\n- A viewer who has not read the post should understand the mood/theme\n- Avoid: random floating icons, word clouds, concept collages\n\nExample output: A lone robot arm precisely placing the final piece of a glowing circuit board, dark studio lighting, shallow depth of field, photorealistic, no text",{cta_field}{hashtags_field}
}}""")

    return "\n".join(parts)


def _parse_response(text: str, config: dict, keyword: str) -> GeneratedPost:
    text = text.strip()
    # Extract just the JSON object — handles markdown code blocks, prose wrapping, trailing backticks
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        text = text[start:end + 1]

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
    variation_index: int = 0,
) -> GeneratedPost:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(post_style=config.get("post_style", "concise, data-driven"))
    user_prompt = _build_user_prompt(keyword, posts, research, config, variation_index)

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


def optimize_post(post: GeneratedPost, config: dict) -> GeneratedPost:
    """Assemble full post text (title first), run LinkedIn optimization, return updated post."""
    # Assemble full text: title → hook → body → CTA → hashtags
    parts = [post.post_title]
    if post.hook:
        parts.append(post.hook)
    parts.append(post.post_text)
    if post.cta_closing:
        parts.append(post.cta_closing)
    if post.hashtags:
        parts.append(" ".join(post.hashtags))
    assembled = "\n\n".join(parts)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    logger.info(f"Optimizing post: '{post.post_title}'")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": f"{OPTIMIZE_PROMPT}\n\n---\n{assembled}"}],
    )
    optimized = response.content[0].text.strip()
    logger.success(f"Post optimized ({len(optimized)} chars): '{post.post_title}'")

    # Return updated post: hook/cta/hashtags are now merged into post_text by the optimizer
    return post.model_copy(update={
        "post_text": optimized,
        "hook": None,
        "cta_closing": None,
        "hashtags": None,
    })
