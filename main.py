import sys
import os
import json
import hashlib
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


def _save_used_urls(keyword: str, new_urls: set) -> None:
    path = _used_urls_path(keyword)
    existing = _load_used_urls(keyword)
    combined = list(existing | new_urls)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2)


def run_pipeline(config: dict) -> None:
    date_from = config["date_from"]
    keywords = config["keywords"]
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
            for post_idx in range(posts_per_keyword):
                logger.info(f"Generating post {post_idx + 1}/{posts_per_keyword} for '{keyword}'")

                # Step 6: Content generation
                post = content_generator.generate(keyword, posts_for_gen, research, config, variation_index=post_idx)

                # Step 6b: Image generation (optional)
                if config.get("image_generation", {}).get("enabled"):
                    image_url = image_generator.generate_and_upload(post.image_prompt, keyword)
                    post = post.model_copy(update={"image_prompt": image_url})

                # Step 7: Write to Google Sheets
                sheets_client.write(post, config)

                # Step 7b: Email notification (optional)
                if config.get("email_notification", {}).get("enabled"):
                    try:
                        import email_notifier
                        email_notifier.send(post, config)
                    except Exception as e:
                        logger.error(f"Email notification failed for '{post.post_title}': {e}")
                        # Non-fatal — Sheets write already succeeded, pipeline continues

            # Persist used post URLs so they are excluded in future runs
            _save_used_urls(keyword, {_post_uid(p) for p in posts_for_gen})
            logger.info(f"Saved {len(posts_for_gen)} used post URLs for '{keyword}'")

            success_count += 1

        except Exception as e:
            logger.error(f"Pipeline failed for '{keyword}': {e}")
            skip_count += 1
            continue

    logger.info(f"{'='*50}")
    logger.info(f"Pipeline complete | success: {success_count}, skipped/failed: {skip_count}")


if __name__ == "__main__":
    config = load_config("config.yaml")
    run_pipeline(config)
