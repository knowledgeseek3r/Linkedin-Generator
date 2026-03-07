from datetime import datetime
from typing import List
from loguru import logger
from models import ScrapedPost


class InsufficientPostsError(Exception):
    pass


def filter_by_date(posts: List[ScrapedPost], date_from: datetime) -> List[ScrapedPost]:
    kept = []
    discarded = 0

    for post in posts:
        post_date = post.date
        # Ensure both are timezone-aware for comparison
        if post_date.tzinfo is None:
            from datetime import timezone
            post_date = post_date.replace(tzinfo=timezone.utc)
        if date_from.tzinfo is None:
            from datetime import timezone
            date_from = date_from.replace(tzinfo=timezone.utc)

        if post_date >= date_from:
            kept.append(post)
        else:
            discarded += 1
            logger.debug(f"Discarded post (outside time range): {post.url} | date={post.date.date()}")

    logger.info(f"Time filter: kept {len(kept)}, discarded {discarded} posts (cutoff: {date_from.date()})")
    return kept
