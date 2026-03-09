# Project Context — LinkedIn Content Intelligence & Generation Pipeline

> Last updated: 2026-03-09
> Status: **6 content quality + optimization features added — end-to-end test pending**

---

## 1. What Has Been Built

A fully automated WAT-framework Python pipeline that:
1. Scrapes top LinkedIn posts per keyword via Apify
2. Filters posts by time range (configurable window)
3. Deduplicates posts across runs (text-hash UID stored in `.tmp/`)
4. Classifies posts for content quality — keeps only educational/news, discards promo/personal
5. Scores posts by engagement (likes × w1 + comments × w2 + shares × w3)
6. Runs web research per keyword via Claude API with `web_search` tool (90s hard timeout)
7. Generates German LinkedIn posts (150–300 words, mobile-optimized) with Hook, CTA, Hashtags
   — Each post gets a distinct content angle (5 rotating angles, no personal storytelling)
   — Content rules: only verified analyst-house case studies, all stats must cite source inline
8. **NEW: Post-optimizer** — second Claude call after generation, before image gen, optimizes for LinkedIn algorithm (hook, structure, CTA, ~1300 chars). Title prepended as first line.
9. Generates a DALL-E 3 image (no text in image enforced via prompt suffix), uploads to imgbb, writes `=IMAGE()` formula to Google Sheets
10. Writes 3-column output to Google Sheets: Thema/Titel | Beitragstext | Beitragsbild
11. Generates N posts per keyword (configurable via `posts_per_keyword`)
12. Sends each post via HTML email (text + image) to recipient for review
13. Reply-triggered LinkedIn posting — reply with trigger word → `reply_checker.py` → posts to LinkedIn API v2

**Confirmed working (2026-03-09):**
- Full pipeline run: "Agentic AI" → 1 post → Sheets → email sent ✅
- Email delivered to raphael.swidnicki@googlemail.com ✅
- LinkedIn API credentials fully configured ✅
- `reply_checker.py --once` ready to test (not yet end-to-end tested with new features)

---

## 2. Architecture Decisions

| Decision | Choice | Reason |
|---|---|---|
| Framework | WAT (Workflows, Agents, Tools) | Separates probabilistic AI from deterministic execution |
| Apify actor | `harvestapi/linkedin-post-search` | Most reliable option |
| Apify input params | `searchQueries: [keyword]`, `maxPosts: n` | Actor ignores `searchQuery`/`maxResults` |
| Date filtering | Post-scrape only (in `time_filter.py`) | No reliable native date filter in Apify |
| Apify fallback | `curious_coder/linkedin-post-search-scraper` | Auto-tried if primary actor fails |
| Claude model | `claude-sonnet-4-6` | Confirmed correct model string |
| Web search tool | `web_search_20260209` | Latest version, no beta header required |
| Research timeout | 90s via `ThreadPoolExecutor.result(timeout=90)` + `shutdown(wait=False)` | Only way to truly abort on Windows |
| Image generation | DALL-E 3 → imgbb | Google Drive rejected service account uploads |
| Image in Sheets | `=IMAGE("url")` formula | Embeds image directly in Sheets cell |
| Classifier strategy | Single batch call per keyword | Cost efficient |
| gspread auth | `service_account_from_dict()` from `credentials.json` | File-based credentials |
| Sheets columns | Fixed 3 columns: Titel, Beitragstext, Beitragsbild | Posting-Zeitpunkt removed |
| Hook/CTA/Hashtags | All merged into Beitragstext (column B) | No separate columns |
| Content rules | `content_rules` flags in config.yaml, injected into generation prompt | Configurable per run, no code branches |
| Post optimizer | Second Claude call (`optimize_post()`), assembles full text then optimizes | Separate step = clean separation of generation vs. polish |
| Title in post text | Prepended before optimizer call | LinkedIn post body should be self-contained |
| No text in images | `_NO_TEXT_SUFFIX` appended to every DALL-E prompt in `image_generator.py` | DALL-E tends to add text unless explicitly blocked |
| Optional features | Controlled exclusively via `config.yaml` flags | Zero code branches if disabled |
| Logging | `loguru` | Dual output: stderr (INFO) + `.tmp/pipeline_DATE.log` (DEBUG) |
| Post deduplication | Text-hash UID (MD5 of first 300 chars) in `.tmp/used_posts_{keyword}.json` | harvestapi returns no URL field |
| Variation angles | 5 predefined angles, rotated by `post_idx` | Prevents identical posts |
| Email sending | SMTP (smtplib) + Gmail App Password | Stdlib, no new dependencies |
| Email reply monitoring | IMAP (imaplib) polling via `reply_checker.py` | Stdlib, simple |
| Pending posts store | `.tmp/pending_posts.json` keyed by Message-ID | Links email reply to post data |
| LinkedIn posting | API v2 `/v2/ugcPosts` + 3-step image upload | Official API with `w_member_social` scope |
| LinkedIn Person URN | `urn:li:person:C40HzWTIZk` | Fetched via `/v2/userinfo` with openid+profile token |
| Person ID discovery | `/v2/userinfo` endpoint requires `openid`+`profile` scopes | `w_member_social` alone is read-only blind |

---

## 3. File Structure & Purpose

```
LinkedIn Content Generator/
├── main.py                  # Orchestrator — pipeline per keyword, dedup, retry, email notification
├── config.yaml              # All settings + optional feature flags
├── config_loader.py         # Loads YAML, resolves ${ENV_VAR}, validates all sections incl. email+linkedin
├── models.py                # Pydantic models for all data types
├── linkedin_scraper.py      # Apify scraping — primary + fallback actor, flexible field mapping
├── time_filter.py           # Filters posts by date_from
├── classifier.py            # Batch Claude classifier — one API call per keyword
├── researcher.py            # Claude with web_search tool, 90s hard timeout
├── content_generator.py     # Variation angles, conditional prompt assembly, JSON retry
├── image_generator.py       # DALL-E 3 image generation + imgbb upload → permanent URL
├── sheets_client.py         # Google Sheets writer — 3 fixed columns, append-only
├── email_notifier.py        # NEW: SMTP HTML email per post → stores Message-ID in pending_posts.json
├── linkedin_poster.py       # NEW: LinkedIn API v2 posting (text-only + 3-step image upload)
├── reply_checker.py         # NEW: IMAP poll → trigger word detection → linkedin_poster call
├── requirements.txt         # All Python dependencies
├── .env                     # API keys (gitignored) — 7 keys configured
├── credentials.json         # Google service account JSON (gitignored)
├── CLAUDE.md                # WAT framework agent instructions
├── CONTEXT.md               # This file
├── workflows/
│   └── linkedin_pipeline.md # WAT SOP
└── .tmp/
    ├── pipeline_YYYY-MM-DD.log
    ├── used_posts_{keyword}.json
    └── pending_posts.json   # NEW: pending email→LinkedIn post queue, keyed by Message-ID
```

---

## 4. Pydantic Models (models.py)

```python
ScrapedPost         # Raw Apify output: text, author, likes, comments, shares, date, url, keyword
ClassifiedPost      # Extends ScrapedPost + post_index, type, keep, reason
ScoredPost          # Extends ClassifiedPost + engagement_score (float)
ResearchSummary     # keyword, sources (List[str]), summary_text
GeneratedPost       # keyword, post_title, post_text, image_prompt,
                    # hook? (Optional), cta_closing? (Optional), hashtags? (Optional[List[str]])
```

---

## 5. Current config.yaml Structure

```yaml
# --- CORE SETTINGS ---
keywords:
  - "Agentic AI"

apify_token: "${APIFY_TOKEN}"
number_of_posts_to_fetch: 20
posts_per_keyword: 1        # set to 5 for full runs
research_depth: shallow
output_sheet_id: "${GOOGLE_SHEET_ID}"
language: "de"
post_style: "thought leadership, concise, data-driven, no fluff"

# --- TIME RANGE FILTER ---
scrape_time_range:
  unit: weeks
  value: 2

# --- OPTIONAL FEATURES ---
generate_hook: true

engagement_scoring:
  enabled: true
  likes_weight: 1
  comments_weight: 3
  shares_weight: 5

voice_samples:
  enabled: false

cta:
  enabled: true
  type: frage_an_community

hashtags:
  enabled: true
  broad_count: 2
  niche_count: 3

content_rules:
  verified_case_studies_only: true
  cite_statistics: true

post_optimization:
  enabled: true

image_generation:
  enabled: true

email_notification:
  enabled: true
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  sender_email: "${EMAIL_USER}"
  sender_password: "${EMAIL_PASSWORD}"
  recipient_email: "${EMAIL_RECIPIENT}"
  reply_trigger: "Post"

linkedin_posting:
  enabled: true
  access_token: "${LINKEDIN_ACCESS_TOKEN}"
  person_urn: "${LINKEDIN_PERSON_URN}"
```

---

## 6. Google Sheets Output Schema

3 fixed columns:

| Column | Header | Content |
|---|---|---|
| A | Thema / Titel | `post_title` |
| B | Beitragstext | `hook` + `\n\n` + `post_text` + `\n\n` + `cta_closing` + `\n\n` + hashtags |
| C | Beitragsbild | `=IMAGE("https://i.ibb.co/...")` formula or raw prompt text |

---

## 7. Pipeline Execution Order (per keyword)

```
1. Load used_urls from .tmp/used_posts_{keyword}.json
2. linkedin_scraper.scrape(keyword, n)          → List[ScrapedPost]
3. time_filter.filter_by_date(posts, date_from)  → List[ScrapedPost]
4. Dedup: filter out posts whose UID is in used_urls  [retry ×2 if <3 fresh]
5. classifier.classify(fresh_posts, keyword)     → List[ClassifiedPost] [retry ×2 if <3]
6. score_posts() in main.py                      → List[ScoredPost]    [only if enabled]
7. researcher.research(keyword, depth)           → ResearchSummary     [90s timeout]
   Loop N times (posts_per_keyword):
8. content_generator.generate(..., variation_index=i) → GeneratedPost
8b. content_generator.optimize_post(post, config)    → GeneratedPost    [only if enabled]
    — assembles title+hook+body+CTA+hashtags, runs LinkedIn optimizer Claude call
    — result: post_text = full optimized text (title first), hook/cta/hashtags = None
8c. image_generator.generate_and_upload(prompt)  → image URL           [only if enabled]
    — _NO_TEXT_SUFFIX appended to every prompt automatically
9. sheets_client.write(post, config)             → None
9b. email_notifier.send(post, config)            → Message-ID          [only if enabled]
   → stores pending post in .tmp/pending_posts.json
   Save UIDs of used posts to .tmp/used_posts_{keyword}.json
```

**Human-in-the-loop publishing:**
```
User replies to email with trigger word
→ python reply_checker.py --once
→ IMAP matches In-Reply-To header to pending_posts.json entry
→ linkedin_poster.post_to_linkedin(post_body, image_url, config)
→ Entry removed from pending_posts.json
```

---

## 8. Content Variation Angles (content_generator.py)

5 angles rotated by `variation_index % 5`:
1. Business case / ROI with numbers
2. Contrarian / myth-busting
3. Step-by-step framework or checklist
4. Trend / prediction / what's changing
5. Analogy / comparison

---

## 9. Email → LinkedIn Flow (new in session 4)

### email_notifier.py
- Assembles post body (hook + text + cta + hashtags) — mirrors `sheets_client._build_row()`
- Sends HTML email via SMTP/STARTTLS (Gmail App Password)
- Stores `{message_id: {post_title, post_body, image_url, sent_at, keyword}}` in `.tmp/pending_posts.json`

### reply_checker.py
- Connects to Gmail IMAP (`imap.gmail.com:993`)
- Searches UNSEEN emails
- Matches `In-Reply-To` header against pending Message-IDs
- Checks first non-empty line for trigger word (case-insensitive)
- On match: calls `linkedin_poster.post_to_linkedin()`, removes from pending, marks email as read
- On 401 (token expired): keeps email unread for retry after token refresh

### linkedin_poster.py
- Text-only post: `POST /v2/ugcPosts` with `shareMediaCategory: NONE`
- Image post: Register upload → PUT image bytes → POST ugcPost with asset URN
- 401 raises `PermissionError` with clear action message

---

## 10. Environment Setup

### .env (7 configured keys)
```
ANTHROPIC_API_KEY=sk-ant-...
APIFY_TOKEN=apify_api_...
GOOGLE_SHEET_ID=1LZ--...
OPENAI_API_KEY=sk-proj-...
IMGBB_API_KEY=767d5f...
EMAIL_USER=raphael.swidnicki@googlemail.com
EMAIL_PASSWORD=mqfj lebw osle ahkx    # Gmail App Password
EMAIL_RECIPIENT=raphael.swidnicki@googlemail.com
LINKEDIN_ACCESS_TOKEN=AQWwfVyrJ...    # w_member_social + openid + profile scopes
LINKEDIN_PERSON_URN=urn:li:person:C40HzWTIZk
```

### LinkedIn App
- App name: PosterApp
- Client ID: 78f68xefx6vkmg
- Company Page: linkedin.com/company/111957742/
- Products: "Share on LinkedIn" + "Sign In with LinkedIn using OpenID Connect"
- Token TTL: 2 months — expires ~2026-05-09
- Person URN discovered via `/v2/userinfo` with openid+profile token

### credentials.json
- Service account: `linkedin-generator@n8n-learning-472012.iam.gserviceaccount.com`

### Python environment
- Python 3.13 (user installation)
- Run: `pip install -r requirements.txt`

---

## 11. Known Issues & Status

| Issue | Status | Details |
|---|---|---|
| Web research timeout | **Known/Handled** | 90s hard limit — pipeline continues without research |
| Engagement scores always 0 | **Open** | harvestapi field names for likes/comments/shares unknown |
| URL field empty | **Mitigated** | text-hash dedup fallback |
| LinkedIn token expiry | **Known** | Tokens expire in ~2 months. Refresh manually at developer.linkedin.com/tools/oauth/token-generator |

---

## 12. Pending Tasks / Next Steps

### High Priority
- [ ] **End-to-end test with all new features**: Run `python main.py` → verify post has title as first line, stat citations, no invented case studies, optimized text → email arrives → reply "Post" → `python reply_checker.py --once` → LinkedIn post live
- [ ] **Reset posts_per_keyword to 5** in config.yaml after test
- [ ] **Re-enable all 3 keywords**: Uncomment RPA and n8n in config.yaml

### Medium Priority
- [ ] **Debug engagement scoring**: Print raw Apify item to confirm field names
- [ ] **Add voice samples**: Set `voice_samples.enabled: true`
- [ ] **Push all changes to GitHub**: `git add . && git commit && git push`

### Low Priority
- [ ] **Add `sortBy: date`** to Apify input
- [ ] **Consider `research_depth: deep`** for higher quality posts
- [ ] **Set up Windows Task Scheduler** for automatic `reply_checker.py` polling

---

## 13. Apify Actor Details

**Primary:** `harvestapi/linkedin-post-search`
- Input: `{"searchQueries": ["keyword"], "maxPosts": 20}`
- Date field: returns a dict `{"timestamp": ms, "date": "ISO", ...}`
- URL field: NOT returned
- Engagement fields: exact names unknown (scores always 0)

**Fallback:** `curious_coder/linkedin-post-search-scraper`

---

## 14. Claude API Usage

| Step | Model | Tool | Cost approx |
|---|---|---|---|
| Classifier | `claude-sonnet-4-6` | none | ~$0.01/keyword |
| Researcher | `claude-sonnet-4-6` | `web_search_20260209` | ~$0.09 for 3 keywords (shallow) |
| Generator | `claude-sonnet-4-6` | none | ~$0.02/post |
| Image | DALL-E 3 | — | $0.04/image |

Full run (3 keywords × 5 posts) ≈ $0.80 total.

---

## 15. Git Repository

- Remote: https://github.com/knowledgeseek3r/Linkedin-Generator
- Last commit: `feat: email notification + LinkedIn human-in-the-loop posting` (session 4)
- **Pending**: push session 4 changes after reply_checker test passes
