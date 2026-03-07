from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class ScrapedPost(BaseModel):
    text: str
    author: str
    likes: int = 0
    comments: int = 0
    shares: int = 0
    date: datetime
    url: str
    keyword: str


class ClassifiedPost(ScrapedPost):
    post_index: int
    type: str   # educational | news | opinion | personal | promo | other
    keep: bool
    reason: str


class ScoredPost(ClassifiedPost):
    engagement_score: float = 0.0


class ResearchSummary(BaseModel):
    keyword: str
    sources: List[str]
    summary_text: str


class GeneratedPost(BaseModel):
    keyword: str
    post_title: str
    post_text: str
    image_prompt: str
    hook: Optional[str] = None
    cta_closing: Optional[str] = None
    hashtags: Optional[List[str]] = None
