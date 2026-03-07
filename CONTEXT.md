# Project Context — LinkedIn Content Intelligence & Generation Pipeline

> Last updated: 2026-03-07
> Status: **Fully working — multiple test runs confirmed end-to-end success**

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
8. Generates a DALL-E 3 image, uploads to imgbb, writes `=IMAGE()` formula to Google Sheets
9. Writes 3-column output to Google Sheets: Thema/Titel | Beitragstext | Beitragsbild
10. Generates N posts per keyword (configurable via `posts_per_keyword`)

**Confirmed working:**
- "Agentic AI" → 5 posts with distinct titles and angles generated and written to Sheets
- Post deduplication via text-hash UID working across runs
- Variation angles produce meaningfully different posts per run

---

## 2. Architecture Decisions

| Decision | Choice | Reason |
|---|---|---|
| Framework | WAT (Workflows, Agents, Tools) | Separates probabilistic AI from deterministic execution |
| Apify actor | `harvestapi/linkedin-post-search` | Most reliable option; `apify/linkedin-post-search-scraper` does NOT exist |
| Apify input params | `searchQueries: [keyword]`, `maxPosts: n` | Actor ignores `searchQuery`/`maxResults` — confirmed by testing |
| Date filtering | Post-scrape only (in `time_filter.py`) | No reliable native date filter in Apify actors |
| Apify fallback | `curious_coder/linkedin-post-search-scraper` | Auto-tried if primary actor fails |
| Claude model | `claude-sonnet-4-6` | Confirmed correct model string as of 2026-03-07 |
| Web search tool | `web_search_20260209` | Latest version, no beta header required |
| Research timeout | 90s via `ThreadPoolExecutor.result(timeout=90)` + `shutdown(wait=False)` | `timeout=` on Anthropic client only applies per HTTP request, not full tool-use loop. ThreadPoolExecutor with `wait=False` is the only way to truly abort on Windows |
| Image generation | DALL-E 3 → imgbb | Google Drive rejected service account uploads (no storage quota). imgbb is free, permanent URLs, simple API |
| Image in Sheets | `=IMAGE("url")` formula | Embeds image directly in Sheets cell |
| Classifier strategy | Single batch call per keyword | All posts classified in one prompt — cost efficient |
| gspread auth | `service_account_from_dict()` from `credentials.json` | File-based credentials per CLAUDE.md convention |
| Sheets columns | Fixed 3 columns: Titel, Beitragstext, Beitragsbild | Posting-Zeitpunkt column removed (not needed) |
| Hook/CTA/Hashtags | All merged into Beitragstext (column B) | Hook prepended, CTA + Hashtags appended — no separate columns |
| Optional features | Controlled exclusively via `config.yaml` flags | Zero code branches run if flag is false/absent |
| Logging | `loguru` | Dual output: stderr (INFO) + `.tmp/pipeline_DATE.log` (DEBUG) |
| File naming | `linkedin_scraper.py` (not `apify_client.py`) | `apify_client.py` conflicts with the `apify-client` package name |
| Post deduplication | Text-hash UID (MD5 of first 300 chars) stored in `.tmp/used_posts_{keyword}.json` | harvestapi returns no URL field — URL-based dedup impossible; text-hash is robust fallback |
| Variation angles | 5 predefined content angles, rotated by `post_idx` | Prevents identical posts across N generations per keyword |
| No personal storytelling | Storytelling angle removed from `VARIATION_ANGLES` | Posts implying personal experience would be fabricated — replaced with analogy/comparison angle |
| JSON extraction | `text.find('{')` / `text.rfind('}')` | More robust than splitting on ``` — handles any wrapping the model adds |
| JSON prompt rule | "Never use double quotes inside string values" | Prevents unescaped quote errors in German text |

---

## 3. File Structure & Purpose

```
LinkedIn Content Generator/
├── main.py                  # Orchestrator — pipeline per keyword, dedup, retry logic, error isolation
├── config.yaml              # All settings + optional feature flags (the only file you need to edit)
├── config_loader.py         # Loads YAML, resolves ${ENV_VAR} references, computes date_from at startup
├── models.py                # Pydantic models for all data types
├── linkedin_scraper.py      # Apify scraping — primary + fallback actor, flexible field mapping, date parsing
├── time_filter.py           # Filters posts by date_from, exports InsufficientPostsError
├── classifier.py            # Batch Claude classifier — one API call per keyword, retry + skip logic
├── researcher.py            # Claude with web_search_20260209 tool, 90s hard timeout via ThreadPoolExecutor
├── content_generator.py     # Variation angles, conditional prompt assembly, generates GeneratedPost, JSON retry
├── image_generator.py       # DALL-E 3 image generation + imgbb upload → permanent URL
├── sheets_client.py         # Google Sheets writer — 3 fixed columns, append-only, exponential backoff
├── requirements.txt         # All Python dependencies with versions
├── .env                     # API keys (gitignored) — 5 keys required
├── credentials.json         # Google service account JSON (gitignored) — must be shared with sheet
├── CLAUDE.md                # WAT framework agent instructions
├── CONTEXT.md               # This file — full project context for session restore
├── workflows/
│   └── linkedin_pipeline.md # WAT SOP — how to run, config reference, setup guide, edge cases
└── .tmp/
    ├── pipeline_YYYY-MM-DD.log       # Daily rotating debug log
    └── used_posts_{keyword}.json     # Per-keyword dedup UIDs (text hashes) — persists across runs
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

All optional feature fields are typed `Optional[...]` with `None` default.

---

## 5. Current config.yaml Structure

```yaml
# --- CORE SETTINGS ---
keywords:
  - "Agentic AI"
  # - "RPA"
  # - "n8n"

apify_token: "${APIFY_TOKEN}"
number_of_posts_to_fetch: 20
posts_per_keyword: 5        # how many LinkedIn posts to generate per keyword (default: 1)
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
  samples: [...]

cta:
  enabled: true
  type: frage_an_community

hashtags:
  enabled: true
  broad_count: 2
  niche_count: 3

image_generation:
  enabled: true
  # Requires OPENAI_API_KEY and IMGBB_API_KEY in .env
```

---

## 6. Google Sheets Output Schema

3 fixed columns:

| Column | Header | Content |
|---|---|---|
| A | Thema / Titel | `post_title` |
| B | Beitragstext | `hook` + `\n\n` + `post_text` + `\n\n` + `cta_closing` + `\n\n` + hashtags |
| C | Beitragsbild | `=IMAGE("https://i.ibb.co/...")` formula or raw prompt text |

- Header row written only once when sheet is empty
- All subsequent runs append rows — never overwrites

---

## 7. Pipeline Execution Order (per keyword)

```
1. Load used_urls from .tmp/used_posts_{keyword}.json
2. linkedin_scraper.scrape(keyword, n)          → List[ScrapedPost]
3. time_filter.filter_by_date(posts, date_from)  → List[ScrapedPost]
4. Dedup: filter out posts whose UID is in used_urls  [retry ×2 with more scraping if <3 fresh]
5. classifier.classify(fresh_posts, keyword)     → List[ClassifiedPost] [retry ×2 if <3]
6. score_posts() in main.py                      → List[ScoredPost]    [only if enabled]
7. researcher.research(keyword, depth)           → ResearchSummary     [90s timeout, graceful fallback]
   Loop N times (posts_per_keyword):
8. content_generator.generate(..., variation_index=i) → GeneratedPost  [each gets a different angle]
8b. image_generator.generate_and_upload(prompt)  → image URL           [only if enabled]
9. sheets_client.write(post, config)             → None
   Save UIDs of used posts to .tmp/used_posts_{keyword}.json
```

Keywords run sequentially. Any keyword failure is caught, logged, and skipped.

---

## 8. Content Variation Angles (content_generator.py)

5 angles rotated by `variation_index % 5`:
1. Business case / ROI with numbers (before/after scenario)
2. Contrarian / myth-busting
3. Step-by-step framework or checklist
4. Trend / prediction / what's changing
5. Analogy / comparison — make abstract concept tangible

**No storytelling angle** — posts implying personal experience would be fabricated.

---

## 9. Post Deduplication (main.py)

- UID per post: `post.url` if non-empty, else `MD5(post.text[:300])`
- harvestapi returns no URL field → always uses MD5 hash
- Stored per-keyword in `.tmp/used_posts_{keyword}.json`
- Loaded at start of each keyword run; saved after all N posts generated successfully
- If too few fresh posts: retry with 2× and 3× more scraping

---

## 10. Key Code Patterns

### Post UID (main.py)
```python
def _post_uid(post) -> str:
    if post.url:
        return post.url
    return hashlib.md5(post.text[:300].encode("utf-8")).hexdigest()
```

### Researcher hard timeout (researcher.py)
```python
executor = ThreadPoolExecutor(max_workers=1)
future = executor.submit(_call_research_api, keyword, max_uses)
try:
    result = future.result(timeout=90)
except TimeoutError:
    future.cancel()
    executor.shutdown(wait=False)
    return fallback_summary
```

### JSON extraction (content_generator.py)
```python
start = text.find('{')
end = text.rfind('}')
if start != -1 and end != -1:
    text = text[start:end + 1]
data = json.loads(text)
```

### Image cell detection (sheets_client.py)
```python
if post.image_prompt.startswith("https://"):
    image_cell = f'=IMAGE("{post.image_prompt}")'
else:
    image_cell = post.image_prompt
```

---

## 11. Environment Setup

### .env (5 required keys)
```
ANTHROPIC_API_KEY=sk-ant-...
APIFY_TOKEN=apify_api_...
GOOGLE_SHEET_ID=1LZ--...
OPENAI_API_KEY=sk-proj-...
IMGBB_API_KEY=767d5f...
```

### credentials.json
- Service account: `linkedin-generator@n8n-learning-472012.iam.gserviceaccount.com`
- Google Cloud project: `n8n-learning-472012`
- Required APIs: Google Sheets API + Google Drive API
- Sheet must be shared with service account (Editor role)
- Service accounts CANNOT upload to Google Drive — use imgbb

### Python environment
- Python 3.13 (user installation: `%APPDATA%\Python\Python313\site-packages`)
- Run: `pip install -r requirements.txt`

### Running
```bash
cd "c:\Users\Raestro\Dropbox\Documents\Projekte\KI\KI Agentic Automations\LinkedIn Content Generator"
python main.py
```

---

## 12. Known Issues & Status

| Issue | Status | Details |
|---|---|---|
| Web research timeout | **Known/Handled** | 90s hard limit via ThreadPoolExecutor — pipeline continues without research |
| `apify_client.py` import conflict | **Fixed** | Renamed to `linkedin_scraper.py` |
| Date dict parsing | **Fixed** | harvestapi returns date as dict — `_parse_date()` handles dicts first |
| Apify wrong input params | **Fixed** | Actor requires `searchQueries: [keyword]` + `maxPosts: n` |
| Google Drive upload | **Fixed** | Service accounts have no Drive quota — replaced with imgbb |
| Hook/CTA duplication | **Fixed** | Prompt explicitly says "Do NOT include in post_text" |
| Engagement scores always 0 | **Open** | harvestapi field names for likes/comments/shares TBD. Scoring runs but all scores = 0. |
| URL field empty | **Mitigated** | harvestapi returns no URL field. Dedup uses text-hash as fallback UID. |
| JSON parse errors | **Fixed** | Robust `{...}` extraction + "no double quotes" prompt rule |
| Identical posts across N generations | **Fixed** | 5 variation angles rotated by post_idx |

---

## 13. Pending Tasks / Next Steps

### High Priority
- [ ] **Debug engagement scoring**: Print raw Apify item to confirm field names for likes/comments/shares. Update `FIELD_MAPS` in `linkedin_scraper.py`
- [ ] **Re-enable all 3 keywords**: Uncomment RPA and n8n in `config.yaml` for full run

### Medium Priority
- [ ] **Add voice samples**: Set `voice_samples.enabled: true` and populate with real LinkedIn posts in user's style
- [ ] **Review generated post quality**: Check Sheet — adjust `post_style` or system prompt if needed
- [ ] **Push all changes to GitHub**: `git add . && git commit && git push`

### Low Priority
- [ ] **Add `sortBy: date`** to Apify input to get newest posts first
- [ ] **Consider `research_depth: deep`** for higher quality posts
- [ ] **Clean up stale cache**: Delete `__pycache__/apify_client.cpython-313.pyc`

---

## 14. Apify Actor Details

**Primary:** `harvestapi/linkedin-post-search`
- Input: `{"searchQueries": ["keyword"], "maxPosts": 20}`
- Date field: returns a dict `{"timestamp": ms, "date": "ISO", ...}`
- URL field: NOT returned (harvestapi does not include post URL)
- Engagement fields: exact names unknown (scores always 0)

**Fallback:** `curious_coder/linkedin-post-search-scraper`

---

## 15. Claude API Usage

| Step | Model | Tool | Cost approx |
|---|---|---|---|
| Classifier | `claude-sonnet-4-6` | none | ~$0.01/keyword |
| Researcher | `claude-sonnet-4-6` | `web_search_20260209` | ~$0.09 for 3 keywords (shallow) |
| Generator | `claude-sonnet-4-6` | none | ~$0.02/post |
| Image | DALL-E 3 | — | $0.04/image |

Full run (3 keywords × 5 posts) ≈ $0.80 total.

---

## 16. Git Repository

- Remote: https://github.com/knowledgeseek3r/Linkedin-Generator
- Auth: PAT stored in git remote URL (local only)
- Last push: initial commit (session 1 only)
- **Pending**: commit + push all session 2+3 changes
