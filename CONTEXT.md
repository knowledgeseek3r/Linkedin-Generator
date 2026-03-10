# Project Context — LinkedIn Content Intelligence & Generation Pipeline

> Last updated: 2026-03-10
> Status: **Session 8 complete — theme history + angle-aware optimizer + stat dedup fix — tested ✅**

---

## 1. What Has Been Built

A fully automated WAT-framework Python pipeline that:
1. Scrapes top LinkedIn posts per keyword via Apify
2. Filters posts by time range (configurable window)
3. Deduplicates posts across runs (text-hash UID stored in `.tmp/`)
4. Classifies posts for content quality — keeps only educational/news, discards promo/personal
5. Scores posts by engagement (likes × w1 + comments × w2 + shares × w3)
6. Runs web research per keyword via Perplexity API (~9s, 7-10 sources)
7. Generates German LinkedIn posts (150–300 words, mobile-optimized) with Hook, CTA, Hashtags
   — Each post gets a distinct content angle (5 rotating angles, persisted across runs)
   — Content rules: only verified analyst-house case studies, all stats must cite source inline
   — No first-person "ich" language — industry analyst / knowledge-article style
   — **Theme history**: last 20 generated posts (cross-keyword) injected as "NICHT WIEDERHOLEN" constraint
   — **Stat dedup**: same statistics/sources explicitly forbidden if they appear in theme history
8. **Post-optimizer** — second Claude call after generation, before image gen (~1300 chars)
   — Now **angle-aware**: preserves structural form matching the generation angle
9. **Ideogram V3 image generation** — 3 images per post using optimized visual metaphor prompt
   — No random text; precise text rules to avoid spelling errors
   — Upload to imgbb for permanent URLs
   — **Optional**: `image_generation.enabled: false` for testing (no images, "Post" reply only)
10. Writes 3-column output to Google Sheets: Thema/Titel | Beitragstext | Beitragsbild (image 1)
11. **3-image email selection** — HTML email shows 3 images side-by-side with labels 1/2/3
    — Reply with `1`, `2`, or `3` → posts selected image to LinkedIn
    — Fallback: reply with trigger word (`Post`) → posts image 1
    — When images disabled: text-only email, "Post" reply only
12. Reply-triggered LinkedIn posting — `reply_checker.py` → posts to LinkedIn API v2
    — IMAP SINCE filter: only checks last 10 days of unread emails (not all 3813)

**Confirmed working (2026-03-10):**
- Full pipeline: "Intelligent Automation" → 3 Ideogram V3 images → email with image grid → reply "3" → Bild 3 auf LinkedIn ✅
- Theme history active: 5+ posts tracked cross-keyword, angle 0-4 cycling correctly ✅
- Image-disabled mode: text-only email, "Post" trigger, no Ideogram calls ✅
- Angle-aware optimizer preserves framework/checklist structure ✅
- Stat/source dedup: 500-char snippet + explicit "keine bereits zitierten Statistiken" rule ✅

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
| Research | Perplexity API (`sonar`/`sonar-pro`) | ~9s vs 90s+ Claude web_search timeout |
| Image generation | Ideogram V3 → imgbb | Higher quality than DALL-E 3; text-capable |
| Image prompt | Detailed visual metaphor rules (no collage, text rules) | Prevents keyword-dump + spelling errors |
| 3 images per post | `generate_multiple(n=3)` in `image_generator.py` | Human selection improves quality |
| Image selection | Reply with `1`/`2`/`3` in email | Simple UX, no extra UI needed |
| Image disabled mode | `image_generation.enabled: false` in config.yaml | Faster/cheaper testing; email uses "Post" trigger only |
| Email image grid | HTML table with 3×180px images + bold number labels | Clear visual selection |
| IMAP filter | `UNSEEN SINCE last-10-days` | Avoids scanning 3813 old emails |
| Image in Sheets | `=IMAGE("url")` formula | Embeds image directly in Sheets cell |
| Classifier strategy | Single batch call per keyword | Cost efficient |
| gspread auth | `service_account_from_dict()` from `credentials.json` | File-based credentials |
| Sheets columns | Fixed 3 columns: Titel, Beitragstext, Beitragsbild | Posting-Zeitpunkt removed |
| Hook/CTA/Hashtags | All merged into Beitragstext (column B) | No separate columns |
| Content rules | `content_rules` flags in config.yaml, injected into generation prompt | Configurable per run |
| Post optimizer | Second Claude call, assembles full text then optimizes | Clean separation |
| Angle-aware optimizer | `variation_index` passed to `optimize_post()`, `ANGLE_STRUCTURE_HINTS` dict | Preserves structural form per angle |
| No-Ich rule | In `SYSTEM_PROMPT_TEMPLATE` — industry analyst style enforced | No personal coaching language |
| Angle tracking | `_load/_save_last_angle()` persisted per keyword in `.tmp/` | Cross-run variation |
| Theme history | `generated_themes.json` in `.tmp/`, last 20 entries, 500-char snippets | Prevents cross-keyword + same-keyword repetition |
| Stat dedup | Explicit rule in BEREITS ABGEDECKT block + 500-char snippet | Prevents same Bitkom/Deloitte stat from repeating |
| Logging | `loguru` | Dual output: stderr (INFO) + `.tmp/pipeline_DATE.log` (DEBUG) |
| Post deduplication | MD5 hash of first 300 chars in `.tmp/used_posts_{keyword}.json` | harvestapi returns no URL |
| Email sending | SMTP (smtplib) + Gmail App Password | Stdlib, no new dependencies |
| Email reply monitoring | IMAP (imaplib) polling via `reply_checker.py` | Stdlib, simple |
| LinkedIn posting | API v2 `/v2/ugcPosts` + 3-step image upload | Official API with `w_member_social` scope |

---

## 3. File Structure & Purpose

```
LinkedIn Content Generator/
├── main.py                  # Orchestrator — pipeline per keyword, dedup, retry, email notification, theme history
├── config.yaml              # All settings + optional feature flags
├── config_loader.py         # Loads YAML, resolves ${ENV_VAR}, validates all sections
├── models.py                # Pydantic models for all data types
├── linkedin_scraper.py      # Apify scraping — primary + fallback actor, flexible field mapping
├── time_filter.py           # Filters posts by date_from
├── classifier.py            # Batch Claude classifier — one API call per keyword
├── researcher.py            # Perplexity API research (sonar/sonar-pro, ~9s)
├── content_generator.py     # Variation angles, ANGLE_STRUCTURE_HINTS, theme history injection, image prompt rules
├── image_generator.py       # Ideogram V3 (3 images) + DALL-E 3 fallback + imgbb upload
├── sheets_client.py         # Google Sheets writer — 3 fixed columns, append-only
├── email_notifier.py        # SMTP HTML email — 3-image grid or text-only (when images disabled)
├── linkedin_poster.py       # LinkedIn API v2 posting (text-only + 3-step image upload)
├── reply_checker.py         # IMAP poll (last 10 days) → 1/2/3 selection → linkedin_poster call
├── requirements.txt         # All Python dependencies
├── .env                     # API keys (gitignored) — 12 keys configured
├── credentials.json         # Google service account JSON (gitignored)
├── CLAUDE.md                # WAT framework agent instructions
├── CONTEXT.md               # This file
├── workflows/
│   └── linkedin_pipeline.md # WAT SOP
└── .tmp/
    ├── pipeline_YYYY-MM-DD.log
    ├── used_posts_{keyword}.json
    ├── last_angle_{keyword}.json
    ├── generated_themes.json    # Cross-keyword theme history (last 20 entries, 500-char snippets)
    └── pending_posts.json       # pending email→LinkedIn post queue, keyed by Message-ID
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
  - "Intelligent Automation"   # currently single keyword for testing

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
  enabled: false   # set to true for production runs
  provider: ideogram

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
| B | Beitragstext | optimized full post text (title first) |
| C | Beitragsbild | `=IMAGE("https://i.ibb.co/...")` formula (image 1) or raw prompt text if images disabled |

---

## 7. Pipeline Execution Order (per keyword)

```
1. Load used_urls from .tmp/used_posts_{keyword}.json
2. linkedin_scraper.scrape(keyword, n)          → List[ScrapedPost]
3. time_filter.filter_by_date(posts, date_from)  → List[ScrapedPost]
4. Dedup: filter out posts whose UID is in used_urls  [retry ×2 if <3 fresh]
5. classifier.classify(fresh_posts, keyword)     → List[ClassifiedPost] [retry ×2 if <3]
6. score_posts() in main.py                      → List[ScoredPost]    [only if enabled]
7. researcher.research(keyword, depth)           → ResearchSummary     [Perplexity, ~9s]
   Load theme_history from .tmp/generated_themes.json
   Loop N times (posts_per_keyword):
8. content_generator.generate(..., variation_index=i, theme_history=history) → GeneratedPost
8b. content_generator.optimize_post(post, config, variation_index=i)        → GeneratedPost
8c. image_generator.generate_multiple(prompt, n=3)                          → List[str]  [only if enabled]
    — Ideogram V3 via https://api.ideogram.ai/v1/ideogram-v3/generate
    — aspect_ratio: 1x1, rendering_speed: QUALITY
    — All 3 uploaded to imgbb for permanent URLs
   _save_theme_entry(keyword, title, post_text[:500])  → generated_themes.json updated
9. sheets_client.write(post, config)             → None  (uses image_urls[0] or raw prompt)
9b. email_notifier.send(post, config, image_urls=[...]) → Message-ID
   Save UIDs of used posts to .tmp/used_posts_{keyword}.json
   Save last angle to .tmp/last_angle_{keyword}.json
```

**Human-in-the-loop publishing:**
```
User replies to email with "1", "2", or "3"  (or just "Post" if images disabled)
→ python reply_checker.py --once
→ IMAP searches UNSEEN emails from last 10 days only
→ Matches In-Reply-To header to pending_posts.json entry
→ Reads first_line: "1"/"2"/"3" → picks image_urls[idx]  |  "Post" → uses image_url (or None)
→ linkedin_poster.post_to_linkedin(post_body, selected_image_url, config)
→ Entry removed from pending_posts.json
```

---

## 8. Content Variation Angles (content_generator.py)

5 angles rotated by `(last_angle + 1 + post_idx) % 5`, persisted per keyword in `.tmp/last_angle_{keyword}.json`:
1. Business case / ROI with numbers
2. Contrarian / myth-busting
3. Step-by-step framework or checklist
4. Trend / prediction / what's changing
5. Analogy / comparison

**ANGLE_STRUCTURE_HINTS** — passed to optimizer so it preserves structural form:
- 0: ROI → lead with numbers, preserve before/after structure
- 1: Contrarian → keep counter-thesis first, no normalization
- 2: Framework → preserve numbered/bullet list, do NOT flatten
- 3: Trend → forward-looking framing stays at front
- 4: Analogy → preserve comparison structure as core

---

## 9. Theme History (main.py + content_generator.py)

**File:** `.tmp/generated_themes.json`
**Schema:** `[{keyword, title, snippet (500 chars), date}, ...]` — max 20 entries, newest first
**Injection:** Before generation, last 10 entries injected as:
```
BEREITS ABGEDECKTE THEMEN — NICHT WIEDERHOLEN:
...
Insbesondere: Verwende KEINE Statistiken oder Quellen die in diesen Posts bereits zitiert wurden
— wähle andere Zahlen, andere Studien, andere Quellen.
- "Title" (keyword, date): snippet...
```
**Saves after:** Each successful post generation (before Sheets write)

---

## 10. Image Generation — Ideogram V3

**API:** `POST https://api.ideogram.ai/v1/ideogram-v3/generate`
- Body (flat JSON, NOT nested in `image_request`): `{"prompt": ..., "aspect_ratio": "1x1", "rendering_speed": "QUALITY"}`
- Auth: `Api-Key: {IDEOGRAM_API_KEY}` header
- Response: `data[0].url` → download bytes → upload to imgbb

**Image prompt rules** (injected via `content_generator.py`):
- ONE visual metaphor, single focal point
- Cinematic/minimalist, photorealistic or clean digital art
- Max 1 word of text; must be short, common English, not from post title
- Default: NO TEXT

**3 images per post:**
- `image_generator.generate_multiple(prompt, keyword, config, n=3)` → `List[str]`
- Image 1 written to Sheets; all 3 stored in `pending_posts.json["image_urls"]`

---

## 11. Email → LinkedIn Flow

### email_notifier.py
- Assembles post body (hook + text + cta + hashtags)
- **With images:** HTML email shows 3 images in table layout (180px each), bold number labels; CTA: "Antworte mit 1, 2 oder 3"
- **Without images:** Text-only email; CTA: "Antworte mit **Post**"
- Stores `{message_id: {post_title, post_body, image_url, image_urls, sent_at, keyword}}` in pending_posts.json

### reply_checker.py
- IMAP `(UNSEEN SINCE "DD-Mon-YYYY")` — last 10 days only
- Matches `In-Reply-To` header against pending Message-IDs
- `first_line in ("1", "2", "3")` AND `image_urls` not empty → `selected_image = image_urls[idx]`
- `reply_trigger` in first_line → uses `image_url` (or None for text-only post)
- On 401: keeps email unread for retry after token refresh

### linkedin_poster.py
- Text-only: `POST /v2/ugcPosts` with `shareMediaCategory: NONE`
- Image: Register upload → PUT image bytes → POST ugcPost with asset URN

---

## 12. Environment Setup

### .env (12 configured keys)
```
ANTHROPIC_API_KEY=sk-ant-...
APIFY_TOKEN=apify_api_...
GOOGLE_SHEET_ID=1LZ--...
OPENAI_API_KEY=sk-proj-...     (kept for DALL-E 3 fallback)
IMGBB_API_KEY=767d5f...
EMAIL_USER=raphael.swidnicki@googlemail.com
EMAIL_PASSWORD=mqfj lebw osle ahkx    # Gmail App Password
EMAIL_RECIPIENT=raphael.swidnicki@googlemail.com
PERPLEXITY_API_KEY=pplx-...
IDEOGRAM_API_KEY=f6E3_...
LINKEDIN_ACCESS_TOKEN=AQWwfVyrJ...    # w_member_social + openid + profile scopes
LINKEDIN_PERSON_URN=urn:li:person:C40HzWTIZk
```

### LinkedIn App
- App name: PosterApp
- Client ID: 78f68xefx6vkmg
- Token TTL: 2 months — expires ~2026-05-09
- Person URN: `urn:li:person:C40HzWTIZk`

### credentials.json
- Service account: `linkedin-generator@n8n-learning-472012.iam.gserviceaccount.com`

### Python environment
- Python 3.13 (user installation)
- Run: `pip install -r requirements.txt`

---

## 13. Known Issues & Status

| Issue | Status | Details |
|---|---|---|
| Web research timeout | **Fixed** | Replaced Claude web_search with Perplexity API (~9s) |
| Engagement scores always 0 | **Open** | harvestapi field names for likes/comments/shares unknown |
| URL field empty | **Mitigated** | text-hash dedup fallback |
| LinkedIn token expiry | **Known** | Tokens expire ~2026-05-09. Refresh at developer.linkedin.com/tools/oauth/token-generator |
| Post similarity cross-keyword | **Fixed (session 8)** | Theme history + angle-aware optimizer |
| Same stat repeating (Bitkom 40,9%) | **Fixed (session 8)** | 500-char snippet + explicit stat-dedup rule |
| Dedup exhaustion during testing | **Known** | Many test runs exhaust the fresh post pool for one keyword; delete `.tmp/used_posts_{keyword}.json` to reset |

---

## 14. Pending Tasks / Next Steps

### High Priority
- [ ] **Reset `posts_per_keyword` to 5** in config.yaml for production runs
- [ ] **Re-enable all keywords** (Agentic AI, RPA/Sovereign AI, n8n) in config.yaml
- [ ] **Re-enable image generation** (`image_generation.enabled: true`) for production
- [ ] **Commit + push session 8 changes** to GitHub

### Medium Priority
- [ ] **Debug engagement scoring**: Print raw Apify item to confirm field names (scores always 0)
- [ ] **Add voice samples**: Set `voice_samples.enabled: true` with real post examples
- [ ] **Set up Windows Task Scheduler** for automatic `reply_checker.py` polling

### Low Priority
- [ ] **Add `sortBy: date`** to Apify input for fresher posts
- [ ] **Consider `research_depth: deep`** for higher quality research (sonar-pro)

---

## 15. Claude API & Cost

| Step | Model/Service | Cost approx |
|---|---|---|
| Classifier | `claude-sonnet-4-6` | ~$0.01/keyword |
| Researcher | Perplexity `sonar` | ~$0.01/keyword |
| Generator | `claude-sonnet-4-6` | ~$0.02/post |
| Optimizer | `claude-sonnet-4-6` | ~$0.02/post |
| Images (3×) | Ideogram V3 QUALITY | ~$0.24 (3 × $0.08) |

Full run (3 keywords × 5 posts) ≈ $4.50 total (image-heavy).
With `provider: dalle3`: ≈ $1.50 total.
Without images: ≈ $0.18 total.

---

## 16. Git Repository

- Remote: https://github.com/knowledgeseek3r/Linkedin-Generator
- Last pushed commit: `feat: Ideogram V3 3-image selection, email grid, reply_checker IMAP fix, content quality rules` (sessions 6+7)
- **Pending push**: session 8 changes (theme history, angle-aware optimizer, stat dedup, image-disabled mode)
