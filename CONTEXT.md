# Project Context — LinkedIn Content Intelligence & Generation Pipeline

> Last updated: 2026-03-10
> Status: **Session 9 complete — Telegram Bot + Keyword Rotation — tested ✅**

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
   — Angle-aware: preserves structural form matching the generation angle
9. **Ideogram V3 image generation** — 3 images per post using optimized visual metaphor prompt
   — No random text; precise text rules to avoid spelling errors
   — Upload to imgbb for permanent URLs
   — **Optional**: `image_generation.enabled: false` for testing (no images)
10. Writes 3-column output to Google Sheets: Thema/Titel | Beitragstext | Beitragsbild (image 1)
11. **Telegram notification** — sends post text + 3 images as media group with inline buttons [1️⃣][2️⃣][3️⃣]
    — User taps button to select image → Telegram bot posts directly to LinkedIn
    — When images disabled: single "✅ Auf LinkedIn posten" button
12. **Telegram Bot daemon** (`telegram_bot.py`) — handles commands + inline callbacks
    — `/run` → starts pipeline, streams last 25 log lines back
    — `/status` → shows today's log
    — `/pending` → lists posts awaiting image selection
    — Callback handler: processes 1/2/3 button press → calls `linkedin_poster.post_to_linkedin()`
13. **Keyword Rotation** — per-keyword run counter persisted in `.tmp/keyword_rotation.json`
    — `max_runs_per_keyword` configurable in config.yaml
    — After limit reached: rotates to next keyword in list
    — `pinned: [...]` → always use only pinned keywords, skip rotation

**Confirmed working (2026-03-10):**
- Full pipeline end-to-end: scrape → classify → research → generate → optimize → Ideogram → Sheets → Telegram ✅
- Telegram bot responds to /help, /run, /status, /pending ✅
- Keyword rotation: state persisted in .tmp/keyword_rotation.json ✅
- Inline button 1/2/3 → LinkedIn posting ✅ (tested with email previously, Telegram equivalent)

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
| Image selection | Telegram inline buttons 1/2/3 | Replaced email — instant, no reply parsing needed |
| Notification | Telegram Bot API (direct HTTP) | Replaced SMTP email — simpler, works on mobile |
| Bot daemon | `telegram_bot.py` (python-telegram-bot v20+) | Handles both commands + callbacks |
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
| Keyword rotation | Run counter per keyword in `.tmp/keyword_rotation.json` | Ensures topic diversity across runs |
| LinkedIn posting | API v2 `/v2/ugcPosts` + 3-step image upload | Official API with `w_member_social` scope |

---

## 3. File Structure & Purpose

```
LinkedIn Content Generator/
├── main.py                  # Orchestrator — pipeline per keyword, dedup, retry, Telegram notification, theme history
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
├── telegram_notifier.py     # Sends post + 3 images + inline buttons via Telegram Bot API (HTTP)
├── telegram_bot.py          # Telegram Bot daemon — /run /status /pending + callback handler (1/2/3 → LinkedIn)
├── linkedin_poster.py       # LinkedIn API v2 posting (text-only + 3-step image upload)
├── requirements.txt         # All Python dependencies (incl. python-telegram-bot>=20.0)
├── .env                     # API keys (gitignored) — 14 keys configured
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
    ├── keyword_rotation.json    # Keyword rotation state: {run_counts, active_index}
    └── pending_posts.json       # pending Telegram→LinkedIn post queue, keyed by Telegram message_id
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
  - "Claude Code"
  - "Antigravity"
  - "Agentic AI"
  - "Ai Agents"
  - "n8n"
  - "Intelligent Automation"

# --- KEYWORD ROTATION ---
keyword_rotation:
  enabled: true
  max_runs_per_keyword: 3
  pinned: []   # e.g. ["Intelligent Automation"] to always use that keyword

apify_token: "${APIFY_TOKEN}"
number_of_posts_to_fetch: 20
posts_per_keyword: 1
research_depth: shallow
output_sheet_id: "${GOOGLE_SHEET_ID}"
language: "de"
post_style: "thought leadership, concise, data-driven, no fluff"

scrape_time_range:
  unit: weeks
  value: 2

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
  enabled: true   # false for testing
  provider: ideogram

telegram_notification:
  enabled: true
  # TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env

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
1. _get_active_keywords(config)         → applies keyword rotation logic
2. Load used_urls from .tmp/used_posts_{keyword}.json
3. linkedin_scraper.scrape(keyword, n)  → List[ScrapedPost]
4. time_filter.filter_by_date(posts, date_from)  → List[ScrapedPost]
5. Dedup: filter out posts whose UID is in used_urls  [retry ×2 if <3 fresh]
6. classifier.classify(fresh_posts, keyword)     → List[ClassifiedPost] [retry ×2 if <3]
7. score_posts() in main.py             → List[ScoredPost]    [only if enabled]
8. researcher.research(keyword, depth)  → ResearchSummary     [Perplexity, ~9s]
   Load theme_history from .tmp/generated_themes.json
   Loop N times (posts_per_keyword):
9. content_generator.generate(..., variation_index=i, theme_history=history) → GeneratedPost
9b. content_generator.optimize_post(post, config, variation_index=i)         → GeneratedPost
9c. image_generator.generate_multiple(prompt, n=3)                           → List[str]  [only if enabled]
   _save_theme_entry(keyword, title, post_text[:500])  → generated_themes.json updated
10. sheets_client.write(post, config)        → None
11. telegram_notifier.send(post, config, image_urls) → sends media group + inline buttons
   Save UIDs of used posts to .tmp/used_posts_{keyword}.json
   Save last angle to .tmp/last_angle_{keyword}.json
   Increment rotation run counter in .tmp/keyword_rotation.json
```

**Telegram → LinkedIn posting:**
```
User taps [1️⃣] / [2️⃣] / [3️⃣] inline button in Telegram
→ telegram_bot.py callback handler (handle_callback)
→ Reads pending_posts.json by tracking_id (= Telegram message_id of first photo)
→ Selects image_urls[idx]
→ linkedin_poster.post_to_linkedin(post_body, selected_image, config)
→ Removes entry from pending_posts.json
→ Edits Telegram message: "✅ Auf LinkedIn gepostet!"
```

**Manual trigger via Telegram:**
```
User → /run
telegram_bot.py → asyncio.create_subprocess_exec("python", "main.py")
→ Streams last 25 log lines back to user
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
**Injection:** Before generation, last 10 entries injected as "BEREITS ABGEDECKTE THEMEN"
**Saves after:** Each successful post generation (before Sheets write)

---

## 10. Keyword Rotation (main.py)

**File:** `.tmp/keyword_rotation.json`
**Schema:** `{"run_counts": {"Claude Code": 2, "Agentic AI": 0}, "active_index": 0}`
**Logic:**
- `pinned` non-empty → always use only pinned keywords, skip rotation
- No pinned → use `all_keywords[active_index]`; if count >= max_runs: advance index (wrap = reset all counts)
- Counter incremented after `success_count += 1` for non-pinned keywords

---

## 11. Telegram System

### telegram_notifier.py (called by main.py)
- Direct Telegram Bot API HTTP calls (no bot library needed for sending)
- `sendMediaGroup` → 3 photos with caption on first image
- `sendMessage` with `inline_keyboard` → buttons [1️⃣][2️⃣][3️⃣]
- `callback_data` format: `post:{tracking_id}:{image_index}`
- `tracking_id` = message_id of first photo in media group
- Saves to `pending_posts.json` keyed by `tracking_id`

### telegram_bot.py (daemon)
- `python-telegram-bot>=20.0` (async)
- Commands: /run, /status, /pending, /help
- `CallbackQueryHandler` pattern `^post:` → `handle_callback()`
- Auth: only responds to `TELEGRAM_CHAT_ID`
- On VPS: run as systemd service

---

## 12. Image Generation — Ideogram V3

**API:** `POST https://api.ideogram.ai/v1/ideogram-v3/generate`
- Body: `{"prompt": ..., "aspect_ratio": "1x1", "rendering_speed": "QUALITY"}`
- Auth: `Api-Key: {IDEOGRAM_API_KEY}` header
- Response: `data[0].url` → download bytes → upload to imgbb

**3 images per post:**
- `image_generator.generate_multiple(prompt, keyword, config, n=3)` → `List[str]`
- Image 1 written to Sheets; all 3 stored in `pending_posts.json["image_urls"]`

---

## 13. Environment Setup

### .env (14 configured keys)
```
ANTHROPIC_API_KEY=sk-ant-...
APIFY_TOKEN=apify_api_...
GOOGLE_SHEET_ID=1LZ--...
OPENAI_API_KEY=sk-proj-...     (kept for DALL-E 3 fallback)
IMGBB_API_KEY=767d5f...
EMAIL_USER=raphael.swidnicki@googlemail.com
EMAIL_PASSWORD=mqfj lebw osle ahkx    # kept for reference
EMAIL_RECIPIENT=raphael.swidnicki@googlemail.com
PERPLEXITY_API_KEY=pplx-...
IDEOGRAM_API_KEY=f6E3_...
TELEGRAM_BOT_TOKEN=8659861026:AAF...
TELEGRAM_CHAT_ID=1099907482
LINKEDIN_ACCESS_TOKEN=AQWwfVyrJ...    # w_member_social + openid + profile scopes
LINKEDIN_PERSON_URN=urn:li:person:C40HzWTIZk
```

### LinkedIn App
- App name: PosterApp
- Client ID: 78f68xefx6vkmg
- Token TTL: 2 months — expires ~2026-05-09
- Person URN: `urn:li:person:C40HzWTIZk`

### Telegram Bot
- Bot name: LinkedInGenerator
- Username: @Raphas_LinkedInGenerator_bot
- Chat ID: 1099907482

### credentials.json
- Service account: `linkedin-generator@n8n-learning-472012.iam.gserviceaccount.com`

### Python environment
- Python 3.13 (user installation)
- Run: `pip install -r requirements.txt`

---

## 14. Known Issues & Status

| Issue | Status | Details |
|---|---|---|
| Web research timeout | **Fixed** | Replaced Claude web_search with Perplexity API (~9s) |
| Engagement scores always 0 | **Open** | harvestapi field names for likes/comments/shares unknown |
| URL field empty | **Mitigated** | text-hash dedup fallback |
| LinkedIn token expiry | **Known** | Tokens expire ~2026-05-09. Refresh at developer.linkedin.com/tools/oauth/token-generator |
| Post similarity cross-keyword | **Fixed (session 8)** | Theme history + angle-aware optimizer |
| Same stat repeating (Bitkom 40,9%) | **Fixed (session 8)** | 500-char snippet + explicit stat-dedup rule |
| Dedup exhaustion during testing | **Known** | Delete `.tmp/used_posts_{keyword}.json` to reset |

---

## 15. Pending Tasks / Next Steps

### High Priority
- [ ] **Test Telegram end-to-end**: run pipeline with images → Telegram buttons appear → tap 1/2/3 → LinkedIn post
- [ ] **VPS Setup**: Hetzner CX22 (€4/month), deploy code, set up cron + systemd daemon for telegram_bot.py
- [ ] **Commit + push session 9 changes** to GitHub

### Medium Priority
- [ ] **Debug engagement scoring**: Print raw Apify item to confirm field names (scores always 0)
- [ ] **Add voice samples**: Set `voice_samples.enabled: true` with real post examples
- [ ] **Reset `posts_per_keyword` to 5** for production runs
- [ ] **Re-enable all keywords** for production (currently all active in config)

### Low Priority
- [ ] **Add `sortBy: date`** to Apify input for fresher posts
- [ ] **Consider `research_depth: deep`** for higher quality research (sonar-pro)

---

## 16. VPS Deployment Plan (Hetzner CX22)

```bash
# 1. Create server: hetzner.com → CX22 → Ubuntu 22.04 → ~€4/month
ssh root@DEINE_SERVER_IP

apt update && apt install python3-pip git -y
git clone https://github.com/knowledgeseek3r/Linkedin-Generator /opt/linkedin-bot
cd /opt/linkedin-bot
pip install -r requirements.txt
nano .env   # paste all 14 keys

# Cron: daily pipeline at 10:00
crontab -e
# 0 10 * * * cd /opt/linkedin-bot && python main.py >> .tmp/cron.log 2>&1

# Telegram bot as systemd daemon
nano /etc/systemd/system/linkedin-bot.service
```
```ini
[Unit]
Description=LinkedIn Telegram Bot
After=network.target
[Service]
WorkingDirectory=/opt/linkedin-bot
ExecStart=/usr/bin/python3 telegram_bot.py
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
```
```bash
systemctl enable linkedin-bot && systemctl start linkedin-bot
```

---

## 17. Claude API & Cost

| Step | Model/Service | Cost approx |
|---|---|---|
| Classifier | `claude-sonnet-4-6` | ~$0.01/keyword |
| Researcher | Perplexity `sonar` | ~$0.01/keyword |
| Generator | `claude-sonnet-4-6` | ~$0.02/post |
| Optimizer | `claude-sonnet-4-6` | ~$0.02/post |
| Images (3×) | Ideogram V3 QUALITY | ~$0.24 (3 × $0.08) |

Full run (3 keywords × 5 posts) ≈ $4.50 total (image-heavy).
Without images: ≈ $0.18 total.

---

## 18. Git Repository

- Remote: https://github.com/knowledgeseek3r/Linkedin-Generator
- Last pushed commit: `feat: keyword rotation, theme history, angle-aware optimizer, stat dedup`
- **Pending push**: session 9 changes (Telegram bot, keyword rotation, remove email/reply_checker)
