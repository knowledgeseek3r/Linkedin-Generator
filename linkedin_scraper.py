import os
from datetime import datetime, timezone
from typing import List
from loguru import logger
from apify_client import ApifyClient
from models import ScrapedPost

PRIMARY_ACTOR = "harvestapi/linkedin-post-search"
FALLBACK_ACTOR = "curious_coder/linkedin-post-search-scraper"

# Field name candidates per actor (tried in order)
FIELD_MAPS = {
    "text": ["text", "postText", "content", "body"],
    "author": ["authorName", "author", "actorName", "name"],
    "likes": ["likesCount", "likes", "likeCount", "numLikes"],
    "comments": ["commentsCount", "comments", "commentCount", "numComments"],
    "shares": ["repostsCount", "shares", "shareCount", "numShares", "repostCount"],
    "date": ["postedAt", "date", "publishedAt", "createdAt", "timestamp"],
    "url": ["postUrl", "url", "link", "postLink", "shareUrl", "postLink", "linkedInUrl", "permalinkUrl"],
}


def _get_field(item: dict, candidates: list, default=None):
    for key in candidates:
        if item.get(key) is not None:
            return item[key]
    return default


def _parse_date(raw) -> datetime:
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw
    # Handle dict like {"timestamp": 1234567890123, "date": "2026-03-06T23:27:25.414Z", ...}
    if isinstance(raw, dict):
        if "timestamp" in raw:
            return datetime.fromtimestamp(raw["timestamp"] / 1000, tz=timezone.utc)
        if "date" in raw:
            raw = raw["date"]
        else:
            logger.warning(f"Could not parse date dict: {raw!r}, using now()")
            return datetime.now(timezone.utc)
    if isinstance(raw, (int, float)):
        # Apify timestamps are in milliseconds when > 1e10
        ts = raw / 1000 if raw > 1e10 else raw
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    logger.warning(f"Could not parse date: {raw!r}, using now()")
    return datetime.now(timezone.utc)


def _map_items(items: list, keyword: str) -> List[ScrapedPost]:
    posts = []
    for item in items:
        text = _get_field(item, FIELD_MAPS["text"], "")
        if not text:
            continue
        posts.append(ScrapedPost(
            text=str(text),
            author=str(_get_field(item, FIELD_MAPS["author"], "Unknown")),
            likes=int(_get_field(item, FIELD_MAPS["likes"], 0) or 0),
            comments=int(_get_field(item, FIELD_MAPS["comments"], 0) or 0),
            shares=int(_get_field(item, FIELD_MAPS["shares"], 0) or 0),
            date=_parse_date(_get_field(item, FIELD_MAPS["date"])),
            url=str(_get_field(item, FIELD_MAPS["url"], "")),
            keyword=keyword,
        ))
    return posts


def _run_actor(client: ApifyClient, actor_id: str, keyword: str, n: int) -> List[ScrapedPost]:
    run_input = {"searchQueries": [keyword], "maxPosts": n}
    logger.info(f"Running Apify actor '{actor_id}' for keyword='{keyword}', n={n}")
    run = client.actor(actor_id).call(run_input=run_input)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    logger.info(f"Apify returned {len(items)} raw items for '{keyword}'")
    return _map_items(items, keyword)


def scrape(keyword: str, n: int) -> List[ScrapedPost]:
    token = os.getenv("APIFY_TOKEN")
    if not token:
        raise EnvironmentError("APIFY_TOKEN is not set in .env")

    client = ApifyClient(token=token)

    try:
        return _run_actor(client, PRIMARY_ACTOR, keyword, n)
    except Exception as e:
        logger.warning(f"Primary actor failed: {e}. Trying fallback actor.")
        try:
            return _run_actor(client, FALLBACK_ACTOR, keyword, n)
        except Exception as e2:
            logger.error(f"Fallback actor also failed: {e2}")
            raise RuntimeError(f"Both Apify actors failed for keyword '{keyword}'") from e2
