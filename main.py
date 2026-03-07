import sys
from loguru import logger
from config_loader import load_config
import linkedin_scraper as apify_client
import time_filter
import classifier
import researcher
import content_generator
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


def run_pipeline(config: dict) -> None:
    date_from = config["date_from"]
    keywords = config["keywords"]
    n_fetch = config["number_of_posts_to_fetch"]
    scoring_cfg = config.get("engagement_scoring", {})

    logger.info(f"Pipeline start | keywords: {keywords} | date_from: {date_from.date()}")

    success_count = 0
    skip_count = 0

    for keyword in keywords:
        logger.info(f"{'='*50}")
        logger.info(f"Processing keyword: '{keyword}'")

        try:
            # Step 1+2: Apify scrape + time filter (with up to 2 retries)
            filtered = []
            for attempt in range(3):
                fetch_n = n_fetch * (attempt + 1)
                raw_posts = apify_client.scrape(keyword, n=fetch_n)
                filtered = time_filter.filter_by_date(raw_posts, date_from)
                if len(filtered) >= 3:
                    break
                if attempt < 2:
                    logger.warning(f"Only {len(filtered)} posts after time filter — retrying with n={fetch_n * 2}")
            else:
                logger.warning(f"Skipping '{keyword}': insufficient posts after time filter ({len(filtered)} < 3)")
                skip_count += 1
                continue

            # Step 3: Content quality classifier (with up to 2 retries)
            classified = None
            for attempt in range(3):
                fetch_n = n_fetch * (attempt + 1)
                if attempt > 0:
                    logger.info(f"Fetching more posts for classifier retry (n={fetch_n})")
                    raw_posts = apify_client.scrape(keyword, n=fetch_n)
                    filtered = time_filter.filter_by_date(raw_posts, date_from)

                classified = classifier.classify(filtered, keyword)
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

            # Step 6: Content generation
            post = content_generator.generate(keyword, posts_for_gen, research, config)

            # Step 7: Write to Google Sheets
            sheets_client.write(post, config)

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
