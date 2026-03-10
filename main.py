import sys
import os
import json
import hashlib
from datetime import date
from loguru import logger
from config_loader import load_config
import linkedin_scraper as apify_client
import time_filter
import classifier
import researcher
import content_generator
import image_generator
import sheets_client
from models import ScoredPost

# Configure loguru
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}", level="INFO")
logger.add(".tmp/pipeline_{time:YYYY-MM-DD}.log", rotation="1 day", level="DEBUG")


def score_posts(posts, scoring_config: dict):
    w_likes = scoring_config.get("likes_weight", 1)
    w_comments = scoring_config.get("comments_weight", 3)
    w_shares = scoring_config.get("shares_weight", 5)

    scored = []
    for post in posts:
        score = (post.likes * w_likes) + (post.comments * w_comments) + (post.shares * w_shares)
        scored.append(ScoredPost(**post.model_dump(), engagement_score=score))

    scored.sort(key=lambda p: p.engagement_score, reverse=True)
    return scored


def _post_uid(post) -> str:
    """Use URL as UID if available, otherwise fall back to a hash of the post text."""
    if post.url:
        return post.url
    return hashlib.md5(post.text[:300].encode("utf-8")).hexdigest()


def _used_urls_path(keyword: str) -> str:
    os.makedirs(".tmp", exist_ok=True)
    safe = keyword.replace(" ", "_").replace("/", "_")
    return f".tmp/used_posts_{safe}.json"


def _load_used_urls(keyword: str) -> set:
    path = _used_urls_path(keyword)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def _load_last_angle(keyword: str) -> int:
    path = f".tmp/last_angle_{keyword.replace(' ', '_').replace('/', '_')}.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("last_angle", -1)
    return -1  # -1 → first run starts at index 0


def _save_last_angle(keyword: str, angle: int) -> None:
    os.makedirs(".tmp", exist_ok=True)
    path = f".tmp/last_angle_{keyword.replace(' ', '_').replace('/', '_')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"last_angle": angle}, f)


_THEMES_FILE = ".tmp/generated_themes.json"
_MAX_THEME_HISTORY = 20


def _load_theme_history() -> list:
    if os.path.exists(_THEMES_FILE):
        with open(_THEMES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_theme_entry(keyword: str, title: str, snippet: str) -> None:
    os.makedirs(".tmp", exist_ok=True)
    history = _load_theme_history()
    history.insert(0, {
        "keyword": keyword,
        "title": title,
        "snippet": snippet[:500],
        "date": str(date.today()),
    })
    history = history[:_MAX_THEME_HISTORY]
    with open(_THEMES_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _save_used_urls(keyword: str, new_urls: set) -> None:
    path = _used_urls_path(keyword)
    existing = _load_used_urls(keyword)
    combined = list(existing | new_urls)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2)


_ROTATION_FILE = ".tmp/keyword_rotation.json"


def _load_rotation_state() -> dict:
    if os.path.exists(_ROTATION_FILE):
        with open(_ROTATION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"run_counts": {}, "active_index": 0}


def _save_rotation_state(state: dict) -> None:
    os.makedirs(".tmp", exist_ok=True)
    with open(_ROTATION_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _get_active_keywords(config: dict) -> list:
    all_keywords = config["keywords"]
    kr_cfg = config.get("keyword_rotation", {})

    if not kr_cfg.get("enabled"):
        return all_keywords

    pinned = kr_cfg.get("pinned", [])

    if pinned:
        logger.info(f"Keyword rotation: using pinned keyword(s): {pinned}")
        return pinned

    max_runs = kr_cfg["max_runs_per_keyword"]
    state = _load_rotation_state()
    run_counts = state.get("run_counts", {})
    active_index = state.get("active_index", 0) % len(all_keywords)
    active_kw = all_keywords[active_index]

    if run_counts.get(active_kw, 0) >= max_runs:
        new_index = (active_index + 1) % len(all_keywords)
        if new_index == 0:
            run_counts = {}
        state["active_index"] = new_index
        state["run_counts"] = run_counts
        _save_rotation_state(state)
        active_kw = all_keywords[new_index]

    logger.info(
        f"Keyword rotation: active='{active_kw}' "
        f"(run {run_counts.get(active_kw, 0) + 1}/{max_runs})"
    )
    return [active_kw]


def run_pipeline(config: dict) -> None:
    date_from = config["date_from"]
    keywords = _get_active_keywords(config)
    n_fetch = config["number_of_posts_to_fetch"]
    scoring_cfg = config.get("engagement_scoring", {})

    logger.info(f"Pipeline start | keywords: {keywords} | date_from: {date_from.date()}")

    posts_per_keyword = config.get("posts_per_keyword", 1)
    success_count = 0
    skip_count = 0

    for keyword in keywords:
        logger.info(f"{'='*50}")
        logger.info(f"Processing keyword: '{keyword}'")

        try:
            # Load previously used post URLs for deduplication
            used_urls = _load_used_urls(keyword)
            logger.info(f"Dedup: {len(used_urls)} previously used posts excluded for '{keyword}'")

            # Step 1+2+dedup: Apify scrape + time filter + URL dedup (with up to 2 retries)
            fresh = []
            for attempt in range(3):
                fetch_n = n_fetch * (attempt + 1)
                raw_posts = apify_client.scrape(keyword, n=fetch_n)
                time_filtered = time_filter.filter_by_date(raw_posts, date_from)
                fresh = [p for p in time_filtered if _post_uid(p) not in used_urls]
                if len(fresh) >= 3:
                    break
                if attempt < 2:
                    logger.warning(f"Only {len(fresh)} fresh posts after dedup — retrying with n={fetch_n * 2}")
            else:
                logger.warning(f"Skipping '{keyword}': insufficient fresh posts after dedup ({len(fresh)} < 3)")
                skip_count += 1
                continue

            # Step 3: Content quality classifier (with up to 2 retries)
            classified = None
            for attempt in range(3):
                fetch_n = n_fetch * (attempt + 1)
                if attempt > 0:
                    logger.info(f"Fetching more posts for classifier retry (n={fetch_n})")
                    raw_posts = apify_client.scrape(keyword, n=fetch_n)
                    time_filtered = time_filter.filter_by_date(raw_posts, date_from)
                    fresh = [p for p in time_filtered if _post_uid(p) not in used_urls]

                classified = classifier.classify(fresh, keyword)
                if classified is not None:
                    break
                if attempt < 2:
                    logger.warning(f"Classifier returned too few posts — retrying with larger fetch")
            else:
                logger.warning(f"Skipping '{keyword}': insufficient quality posts after classifier retries")
                skip_count += 1
                continue

            # Step 4: Engagement scoring (optional)
            if scoring_cfg.get("enabled"):
                posts_for_gen = score_posts(classified, scoring_cfg)
                logger.info(f"Engagement scoring applied — top score: {posts_for_gen[0].engagement_score:.0f}")
            else:
                posts_for_gen = classified

            # Step 5: Web research
            research = researcher.research(keyword, config.get("research_depth", "shallow"))

            # Steps 6+7: Generate and write N posts per keyword
            last_angle = _load_last_angle(keyword)
            theme_history = _load_theme_history()
            logger.debug(f"Theme history: {len(theme_history)} entries loaded")
            for post_idx in range(posts_per_keyword):
                variation_index = (last_angle + 1 + post_idx) % 5
                logger.info(f"Generating post {post_idx + 1}/{posts_per_keyword} for '{keyword}' (angle {variation_index})")

                # Step 6: Content generation
                post = content_generator.generate(keyword, posts_for_gen, research, config, variation_index=variation_index, theme_history=theme_history)

                # Step 6b: Post optimization (optional) — runs BEFORE image generation
                if config.get("post_optimization", {}).get("enabled"):
                    post = content_generator.optimize_post(post, config, variation_index=variation_index)

                # Step 6c: Image generation — 3 images for selection (optional)
                image_urls = []
                if config.get("image_generation", {}).get("enabled"):
                    image_urls = image_generator.generate_multiple(post.image_prompt, keyword, config, n=3)
                    post = post.model_copy(update={"image_prompt": image_urls[0]})

                # Save theme entry so future runs avoid repeating these arguments
                _save_theme_entry(keyword, post.post_title, post.post_text)

                # Step 7: Write to Google Sheets (uses first image = post.image_prompt)
                sheets_client.write(post, config)

                # Step 7b: Email notification (optional)
                if config.get("email_notification", {}).get("enabled"):
                    try:
                        import email_notifier
                        email_notifier.send(post, config, image_urls=image_urls)
                    except Exception as e:
                        logger.error(f"Email notification failed for '{post.post_title}': {e}")
                        # Non-fatal — Sheets write already succeeded, pipeline continues

            # Persist last used angle so next run rotates to a different angle
            _save_last_angle(keyword, (last_angle + posts_per_keyword) % 5)

            # Persist used post URLs so they are excluded in future runs
            _save_used_urls(keyword, {_post_uid(p) for p in posts_for_gen})
            logger.info(f"Saved {len(posts_for_gen)} used post URLs for '{keyword}'")

            success_count += 1

            # Increment rotation run counter for non-pinned keywords
            kr_cfg = config.get("keyword_rotation", {})
            if kr_cfg.get("enabled") and not kr_cfg.get("pinned"):
                state = _load_rotation_state()
                state.setdefault("run_counts", {})[keyword] = \
                    state["run_counts"].get(keyword, 0) + 1
                _save_rotation_state(state)

        except Exception as e:
            logger.error(f"Pipeline failed for '{keyword}': {e}")
            skip_count += 1
            continue

    logger.info(f"{'='*50}")
    logger.info(f"Pipeline complete | success: {success_count}, skipped/failed: {skip_count}")


if __name__ == "__main__":
    config = load_config("config.yaml")
    run_pipeline(config)
