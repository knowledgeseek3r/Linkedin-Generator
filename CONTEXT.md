# Project Context — LinkedIn Content Intelligence & Generation Pipeline

> Last updated: 2026-03-07
> Status: **Fully built and first run completed successfully**

---

## 1. What Has Been Built

A fully automated WAT-framework Python pipeline that:
1. Scrapes top LinkedIn posts per keyword via Apify
2. Filters posts by time range (configurable window)
3. Classifies posts for content quality — keeps only educational/news, discards promo/personal
4. Scores posts by engagement (likes × w1 + comments × w2 + shares × w3)
5. Runs web research per keyword via Claude API with `web_search` tool
6. Generates German LinkedIn posts (150–300 words, mobile-optimized) with all optional features
7. Writes output rows to Google Sheets with dynamic columns

**First run confirmed working end-to-end:**
- "Agentic AI" → post generated and written to Google Sheets ✅
  - Title: *"Agentic AI: Warum dein nächster Mitarbeiter kein Mensch ist"*
- "RPA" → in progress during first run
- "n8n" → in progress during first run

---

## 2. Architecture Decisions

| Decision | Choice | Reason |
|---|---|---|
| Framework | WAT (Workflows, Agents, Tools) | Separates probabilistic AI from deterministic execution |
| Apify actor | `harvestapi/linkedin-post-search` | The actor ID `apify/linkedin-post-search-scraper` does NOT exist. harvestapi is the most reliable option. |
| Apify input params | `searchQueries: [keyword]`, `maxPosts: n` | Actor ignores `searchQuery`/`maxResults` — these are the correct param names confirmed by testing |
| Date filtering | Post-scrape only (in `time_filter.py`) | No reliable native date filter in Apify actors |
| Apify fallback | `curious_coder/linkedin-post-search-scraper` | Auto-tried if primary actor fails |
| Claude model | `claude-sonnet-4-6` | Confirmed correct model string as of 2026-03-07 |
| Web search tool | `web_search_20260209` | Latest version, no beta header required |
| Research timeout | 120 seconds | Initial run showed 30-min default timeout caused pipeline stall |
| Classifier strategy | Single batch call per keyword | All posts classified in one prompt — cost efficient |
| gspread auth | `service_account_from_dict()` from `credentials.json` | File-based credentials per CLAUDE.md convention |
| Sheets columns | Dynamically built based on active config flags | No empty columns when features are disabled |
| Optional features | Controlled exclusively via `config.yaml` flags | Zero code branches run if flag is false/absent |
| Logging | `loguru` | Dual output: stderr (INFO) + `.tmp/pipeline_DATE.log` (DEBUG) |
| File naming | `linkedin_scraper.py` (not `apify_client.py`) | `apify_client.py` conflicts with the `apify-client` package name — Python picked up local file instead of installed package |

---

## 3. File Structure & Purpose

```
LinkedIn Content Generator/
├── main.py                  # Orchestrator — runs full pipeline per keyword, retry logic, error isolation
├── config.yaml              # All settings + optional feature flags (the only file you need to edit)
├── config_loader.py         # Loads YAML, resolves ${ENV_VAR} references, computes date_from at startup
├── models.py                # Pydantic models for all data types
├── linkedin_scraper.py      # Apify scraping — primary + fallback actor, flexible field mapping, date parsing
├── time_filter.py           # Filters posts by date_from, exports InsufficientPostsError
├── classifier.py            # Batch Claude classifier — one API call per keyword, retry + skip logic
├── researcher.py            # Claude with web_search_20260209 tool, 120s timeout, graceful fallback
├── content_generator.py     # Conditional prompt assembly, generates GeneratedPost, JSON retry
├── sheets_client.py         # Google Sheets writer — dynamic headers, append-only, exponential backoff
├── requirements.txt         # All Python dependencies with versions
├── .env                     # API keys (gitignored) — 3 keys required
├── credentials.json         # Google service account JSON (gitignored) — must be shared with sheet
├── CLAUDE.md                # WAT framework agent instructions
├── CONTEXT.md               # This file — full project context for session restore
├── workflows/
│   └── linkedin_pipeline.md # WAT SOP — how to run, config reference, setup guide, edge cases
└── .tmp/
    └── pipeline_YYYY-MM-DD.log  # Daily rotating debug log
```

**Note:** `apify_client.py` was renamed to `linkedin_scraper.py` to avoid Python import conflict with the installed `apify-client` package. The import in `main.py` uses `import linkedin_scraper as apify_client` to keep internal naming consistent.

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

All optional feature fields are typed `Optional[...]` with `None` default — they are only populated when the corresponding config flag is `true`.

---

## 5. Current config.yaml Structure

```yaml
# --- CORE SETTINGS ---
keywords:
  - "Agentic AI"
  - "RPA"
  - "n8n"

apify_token: "${APIFY_TOKEN}"          # resolved from .env at runtime
number_of_posts_to_fetch: 20           # before filtering; retries double this
research_depth: shallow                # shallow = 3 web searches, deep = 10
output_sheet_id: "${GOOGLE_SHEET_ID}"  # resolved from .env at runtime
language: "de"
post_style: "thought leadership, concise, data-driven, no fluff"

# --- TIME RANGE FILTER ---
scrape_time_range:
  unit: weeks        # days | weeks | months
  value: 2           # resolved to: date_from = now() - 14 days

# --- OPTIONAL FEATURES ---
generate_hook: true                    # Column E in Sheets

engagement_scoring:
  enabled: true
  likes_weight: 1
  comments_weight: 3
  shares_weight: 5

voice_samples:
  enabled: false                       # set true + fill samples to inject writing style
  samples:
    - "Beispiel-Post 1 Text hier..."

cta:
  enabled: true
  type: frage_an_community             # frage_an_community | ressource_teilen |
                                       # meinung_einfordern | newsletter_link

hashtags:
  enabled: true
  broad_count: 2
  niche_count: 3                       # Column F in Sheets
```

**Env var resolution:** `${APIFY_TOKEN}` and `${GOOGLE_SHEET_ID}` are replaced at runtime by `config_loader.py` reading from `.env`.

**`date_from` computation:** Done once at startup in `config_loader.py`:
```python
days = value * UNIT_TO_DAYS[unit]   # weeks → 7 days each
config["date_from"] = datetime.now(timezone.utc) - timedelta(days=days)
```

---

## 6. Google Sheets Output Schema

Columns are written dynamically based on active features:

| Column | Header | Content | Always? |
|---|---|---|---|
| A | Thema / Titel | `post_title` | Yes |
| B | Beitragstext | `post_text` + `\n\n` + `cta_closing` (if enabled) | Yes |
| C | Image Prompt | `image_prompt` (English, DALL-E/Midjourney style) | Yes |
| D | Posting-Zeitpunkt | Hardcoded "Di–Do 08:00–10:00" | Yes |
| E | Hook | Standalone first 210 chars (independent from post_text) | Only if `generate_hook: true` |
| F | Hashtags | Space-separated string e.g. `#AI #Automatisierung #AgenticAI` | Only if `hashtags.enabled: true` |

- Header row is written only once, on the first run when sheet is empty
- All subsequent runs append rows — never overwrites
- CTA closing sentence is appended to the post body in column B (not a separate column)

---

## 7. Pipeline Execution Order (per keyword)

```
1. linkedin_scraper.scrape(keyword, n)         → List[ScrapedPost]
2. time_filter.filter_by_date(posts, date_from) → List[ScrapedPost]  [retry ×2 if <3]
3. classifier.classify(posts, keyword)          → List[ClassifiedPost] [retry ×2 if <3]
4. score_posts() in main.py                     → List[ScoredPost]    [only if enabled]
5. researcher.research(keyword, depth)          → ResearchSummary     [graceful fallback]
6. content_generator.generate(...)              → GeneratedPost
7. sheets_client.write(post, config)            → None
```

Keywords run sequentially. Any keyword failure is caught, logged, and skipped — next keyword continues.

---

## 8. Key Code Patterns

### Optional feature injection (content_generator.py)
Every optional feature checks its config flag before adding to the prompt or output schema:
```python
if config.get("generate_hook"):
    hook_field = '"hook": "...",'          # added to JSON schema in prompt

if config.get("cta", {}).get("enabled"):
    parts.append(f"CTA instruction: ...")  # added to prompt

if voice.get("enabled") and voice.get("samples"):
    for sample in samples: ...             # injected into prompt
```
If a flag is `false` or absent: zero code branches execute, no empty fields written.

### Apify field mapping (linkedin_scraper.py)
Flexible multi-key lookup handles different field names across actors:
```python
FIELD_MAPS = {
    "likes": ["likesCount", "likes", "likeCount", "numLikes"],
    ...
}
def _get_field(item, candidates, default=None):
    for key in candidates:
        if item.get(key) is not None: return item[key]
    return default
```

### Date parsing (linkedin_scraper.py)
Apify's `harvestapi` actor returns dates as a dict:
`{"timestamp": 1772839645414, "date": "2026-03-06T23:27:25.414Z", "postedAgoShort": "13h", ...}`
The parser extracts `timestamp` (milliseconds → seconds) first, then falls back to ISO string parsing.

### Retry pattern (main.py)
```python
for attempt in range(3):
    raw_posts = scrape(keyword, n=n_fetch * (attempt + 1))  # doubles each retry
    filtered = filter_by_date(raw_posts, date_from)
    if len(filtered) >= 3: break
else:
    # for-else: runs only if loop never broke
    logger.warning("Skipping keyword...")
    continue
```

### Dynamic Sheets columns (sheets_client.py)
Row is built as a list — optional columns appended only when feature is active:
```python
row = [post_title, body, image_prompt, posting_time]   # always
if generate_hook: row.append(hook)
if hashtags.enabled: row.append(" ".join(hashtags))
```
Headers are built with the same conditional logic, ensuring alignment.

---

## 9. Environment Setup

### .env (required keys)
```
ANTHROPIC_API_KEY=sk-ant-api03-...
APIFY_TOKEN=apify_api_...
GOOGLE_SHEET_ID=1LZ--hzKT...
# GSPREAD_CREDENTIALS_PATH=credentials.json  (optional override)
```

### credentials.json
- Service account: `linkedin-generator@n8n-learning-472012.iam.gserviceaccount.com`
- Google Cloud project: `n8n-learning-472012`
- Required APIs: Google Sheets API + Google Drive API (both must be enabled)
- The Google Sheet must be shared with the service account email above (Editor role)
- File lives in project root, gitignored

### Python environment
- Python 3.13 (user installation: `%APPDATA%\Python\Python313\site-packages`)
- All deps installed: `pip install -r requirements.txt`
- Key versions: `anthropic==0.77.1`, `apify-client==2.5.0`, `gspread==6.2.1`, `loguru==0.7.3`

### Running
```bash
cd "c:\Users\Raestro\Dropbox\Documents\Projekte\KI\KI Agentic Automations\LinkedIn Content Generator"
python main.py
```

---

## 10. Known Issues & Open Questions

### Known Issues

| Issue | Status | Details |
|---|---|---|
| Web research timeout | **Fixed** | Default Anthropic client timeout caused 30-min stalls. Now `timeout=120.0` in `researcher.py` |
| `apify_client.py` import conflict | **Fixed** | Renamed to `linkedin_scraper.py`; `main.py` uses `import linkedin_scraper as apify_client` |
| Date dict parsing | **Fixed** | harvestapi returns date as `{"timestamp": ms, "date": "ISO"}` dict, not a string. `_parse_date()` now handles dicts first |
| Apify wrong input params | **Fixed** | Actor requires `searchQueries: [keyword]` + `maxPosts: n`, not `searchQuery`/`maxResults` |
| Engagement scores always 0 | **Open** | harvestapi may return likes/comments/shares under different field names than expected. Posts still work — scoring just doesn't rank them. Investigate actual field names from Apify response |

### Open Questions

1. **Engagement score = 0**: The `likesCount`/`commentsCount`/`repostsCount` field names in `FIELD_MAPS` may not match what harvestapi actually returns. To debug: print a raw item from Apify and check exact keys.

2. **Web search tool on current account tier**: The `web_search_20260209` tool timed out in the first run (30min). With the 120s fix, it will either work or fall back gracefully. If it consistently fails, consider whether the Anthropic account has web search access enabled.

3. **Research quality without web search**: When research fails/times out, the generation prompt gets `"No research available for {keyword} due to API error."` as the research section. Posts are still generated but less data-driven.

4. **`__pycache__/apify_client.cpython-313.pyc`**: A stale `.pyc` cache exists for the old `apify_client.py`. This is harmless but can be deleted.

---

## 11. Pending Tasks / Next Steps

### High Priority
- [ ] **Debug engagement scoring**: Print a raw Apify item to confirm exact field names for likes/comments/shares. Update `FIELD_MAPS` in `linkedin_scraper.py` if needed.
- [ ] **Verify web search works with 120s timeout**: Run the pipeline fresh and check if research succeeds. If still timing out, investigate account web search access.

### Medium Priority
- [ ] **Add voice samples**: Set `voice_samples.enabled: true` in `config.yaml` and populate with real LinkedIn posts written in the user's style to personalize generation.
- [ ] **Test all CTA types**: Currently using `frage_an_community`. Test `ressource_teilen`, `meinung_einfordern`, `newsletter_link`.
- [ ] **Review first generated post quality**: Check the Google Sheet to evaluate post quality — adjust `post_style` or system prompt if needed.
- [ ] **Clean up stale cache**: Delete `__pycache__/apify_client.cpython-313.pyc` (old file from before rename).

### Low Priority
- [ ] **Add `.gitignore`**: Should exclude `.env`, `credentials.json`, `__pycache__/`, `.tmp/`
- [ ] **Add `sortBy: date`** to Apify input to get newest posts first (currently returns by relevance)
- [ ] **Consider `research_depth: deep`** for higher quality posts — currently using `shallow` (3 searches)

---

## 12. Apify Actor Details

**Primary:** `harvestapi/linkedin-post-search`
- Input: `{"searchQueries": ["keyword"], "maxPosts": 20}`
- Returns: ~50 posts per page, up to 400+ per keyword available
- Date field: returns a dict `{"timestamp": ms, "date": "ISO", "postedAgoShort": "13h", ...}`
- Engagement fields: exact names TBD (see open issue above)
- Cost: ~$2 per 1,000 posts

**Fallback:** `curious_coder/linkedin-post-search-scraper`
- Auto-tried if primary actor raises any exception
- Different input schema — may need adjustment if primary fails consistently

---

## 13. Claude API Usage

| Step | Model | Tool | Tokens approx |
|---|---|---|---|
| Classifier | `claude-sonnet-4-6` | none | ~2k input + ~500 output per keyword |
| Researcher | `claude-sonnet-4-6` | `web_search_20260209` | ~3k input + ~2k output + $10/1000 searches |
| Generator | `claude-sonnet-4-6` | none | ~4k input + ~1k output per keyword |

Web search pricing: **$10 per 1,000 searches**. With `shallow` (3 searches) and 3 keywords = 9 searches per full run ≈ $0.09 in search costs per run.

---

## 14. WAT Framework Notes

This project follows the WAT framework defined in `CLAUDE.md`:
- **Workflows** → `workflows/linkedin_pipeline.md` (the SOP)
- **Agent** → Claude (you, the AI) — reads workflow, decides what to run
- **Tools** → all `.py` files except `main.py` and `models.py`

`main.py` is the orchestrator — it is not a "tool" in the WAT sense but the agent harness that sequences tool calls.

When adding new features, update `workflows/linkedin_pipeline.md` to reflect the new behavior. Never hardcode what should be configurable.
