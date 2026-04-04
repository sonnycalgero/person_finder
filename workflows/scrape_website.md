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
- **403/bot block**: The site blocks scrapers. Note this to the user and suggest they visit manually.
- **JS-heavy sites**: BeautifulSoup can't execute JavaScript. If a page returns minimal HTML, the actual content may be loaded by JS — note this and try a different URL (like `/contact`).
- **Multiple locations**: Some businesses list many addresses. Report the first 3 and note if more exist.

## Output Format
Results are auto-saved to `.tmp/<name>_<timestamp>.txt` unless `--output` is specified.
Each save **appends** to the file, not overwrites.
