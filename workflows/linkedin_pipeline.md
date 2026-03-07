# Workflow: LinkedIn Content Intelligence & Generation Pipeline

## Objective
Generate German-language LinkedIn posts by scraping top-performing posts via Apify, filtering them by time and content quality, running web research, and writing AI-generated posts to Google Sheets.

## Required Inputs
- `config.yaml` with all settings configured
- `.env` with: `ANTHROPIC_API_KEY`, `APIFY_TOKEN`, `GOOGLE_SHEET_ID`
- `credentials.json` — Google service account JSON (share the target Sheet with the service account email)

## Tools Used
| Tool | Purpose |
|---|---|
| `apify_client.py` | Scrape LinkedIn posts by keyword via Apify |
| `time_filter.py` | Filter posts to configured time window |
| `classifier.py` | Batch-classify posts (keep educational/news, discard personal/promo) |
| `researcher.py` | Web research via Claude API with web_search tool |
| `content_generator.py` | Generate German LinkedIn post with optional features |
| `sheets_client.py` | Append output row to Google Sheets |

## Pipeline Execution Order (per keyword)
1. **Apify scrape** → raw posts (n = `number_of_posts_to_fetch`)
2. **Time range filter** → discard posts older than `date_from` (resolved at startup from `scrape_time_range`)
3. **Content quality classifier** → discard personal/promo posts (batch Claude call)
4. **Engagement scoring** (if `engagement_scoring.enabled`) → rank by weighted score
5. **Web research** → structured summary from `research_depth` sources
6. **Content generation** → German post with all active optional features
7. **Write to Google Sheets** → append row with dynamic columns

## Running the Pipeline
```bash
cd "LinkedIn Content Generator"
pip install -r requirements.txt
python main.py
```

## Config Reference

| Key | Description |
|---|---|
| `keywords` | List of topics to process |
| `number_of_posts_to_fetch` | Posts fetched from Apify before filtering |
| `research_depth` | `shallow` (3 sources) or `deep` (8+ sources) |
| `scrape_time_range` | `unit` (days/weeks/months) + `value` — posts older than this are discarded |
| `generate_hook` | If true: write standalone hook (max 210 chars) to Column E |
| `engagement_scoring.enabled` | If true: rank posts by weighted likes/comments/shares |
| `voice_samples.enabled` | If true: inject writing style examples into generation prompt |
| `cta.enabled` | If true: add CTA closing sentence to post |
| `cta.type` | `frage_an_community` \| `ressource_teilen` \| `meinung_einfordern` \| `newsletter_link` |
| `hashtags.enabled` | If true: write hashtag list to Column F |

## Google Sheets Output

| Column | Content | Condition |
|---|---|---|
| A | Post title (Thema / Titel) | Always |
| B | Full post text + CTA | Always |
| C | Image generation prompt | Always |
| D | Recommended posting time | Always |
| E | Hook (first 210 chars, standalone) | Only if `generate_hook: true` |
| F | Hashtags | Only if `hashtags.enabled: true` |

## Retry Logic
- **Time filter < 3 posts**: retry Apify scrape up to 2x with doubled fetch count
- **Classifier < 3 qualified posts**: retry Apify scrape up to 2x with larger fetch
- **Still insufficient**: skip keyword, log warning, continue with next
- **Apify actor failure**: try fallback actor `curious_coder/linkedin-post-search-scraper`
- **Claude API error**: retry once; if still failing, skip keyword

## Google Sheets Setup
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Enable **Google Sheets API** and **Google Drive API**
3. Create a Service Account → download JSON → save as `credentials.json` in project root
4. Open your Google Sheet → Share it with the `client_email` from `credentials.json`
5. Copy the Sheet ID from the URL into `.env` as `GOOGLE_SHEET_ID`

## Known Constraints
- Apify LinkedIn actors: max ~400 posts per search query
- Date filtering is post-scrape (no reliable native Apify date filter)
- Web search: $10 per 1,000 searches — use `shallow` depth for cost efficiency
- gspread rate limit: 300 requests/60s — pipeline adds `sleep(2)` between writes
- Image generation is NOT performed — only the prompt is written to Sheets

## Edge Cases
- If a keyword produces zero Apify results: logged as error, pipeline continues
- If web research fails (API error): pipeline continues with empty research summary
- If Sheets write fails after 3 retries: error logged, pipeline continues with next keyword
- All keys in `.env` must be set before running — missing keys cause immediate startup failure
