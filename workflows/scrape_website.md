# Workflow: Scrape Single Website

## Objective
Extract all contact information (phone, email, address, name) from a specific URL.

## Required Inputs
- `url` — the full URL to scrape (e.g., `https://acme.com/contact`)

## Optional Inputs
- `output` — file path to save results (defaults to auto-save in `.tmp/`)

## Tool
`tools/scrape_single_site.py`

## Steps

1. **Confirm the URL is accessible** — if the user gave a business name, not a URL, use `workflows/find_contact_info.md` instead.

2. **Run the scraper:**
   ```bash
   python tools/scrape_single_site.py --url "<URL>" [--output .tmp/results.txt]
   ```

3. **Read the output** — it will show:
   - Business/person name (from JSON-LD, og:site_name, or `<title>`)
   - Phone numbers (all formats normalized)
   - Email addresses
   - Physical address (if found)
   - Social media links

4. **If output is sparse**, try the contact page directly:
   - Common patterns: `/contact`, `/contact-us`, `/about`, `/about-us`
   - Run the scraper again on that specific page

5. **Present results** using the concise format:
   ```
   [Name]
     Phone:   (xxx) xxx-xxxx
     Email:   info@example.com
     Address: 123 Main St, City, ST 12345
     Sources: 1 page checked
   ```

## Edge Cases
- **403/bot block**: The tool has a three-tier fetch pipeline:
  1. **Proxy** (ScraperAPI / Scrape.do) — for known bot-protected domains (Whitepages, Spokeo, etc.)
  2. **Direct requests** — for open sites, with rotated User-Agent headers
  3. **Playwright + stealth** — auto-escalation when the first two return thin/blocked content
  If a page returns a Cloudflare challenge, CAPTCHA, homepage redirect, or fewer than 200 characters of visible text, the tool detects this and escalates to a stealth Chromium browser automatically. Look for `[WARNING: thin content — ...]` or `[escalating to Playwright]` in stderr.
- **JS-heavy sites**: For known JS-heavy domains (Radaris, Spokeo), the proxy enables JS rendering automatically. For other sites that return empty shells, Playwright handles JS rendering locally (no proxy credits consumed).
- **Multiple locations**: Some businesses list many addresses. Report the first 3 and note if more exist.
- **Proxy provider priority**: `SCRAPER_API_KEY` (ScraperAPI) takes precedence. If absent, `SCRAPE_DO_TOKEN` (Scrape.do) is used. Both are optional — open sites always fetch directly with no credits consumed.
- **Retry on transient errors**: All HTTP requests (proxy, direct, API) automatically retry up to 3 times with exponential backoff (2s, 4s, 8s) on 502, 503, 429, and connection errors.
- **Playwright not installed**: If `playwright` and `playwright-stealth` aren't installed, everything works as before — the browser escalation path is simply skipped. To install: `pip install playwright playwright-stealth && playwright install chromium`.

## Output Format
Results are auto-saved to `.tmp/<name>_<timestamp>.txt` unless `--output` is specified.
Each save **appends** to the file, not overwrites.
