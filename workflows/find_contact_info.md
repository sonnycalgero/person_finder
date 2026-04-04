# Workflow: Find Business / Organization Contact Info

## Objective
Find verified phone numbers, emails, addresses, and websites for a business or organization.
Cross-verify across multiple sources and report confidence.

## Required Inputs
- `query` — business name, optionally with city/state (e.g., `"Acme Corp Denver CO"`)

## Optional Inputs
- `limit` — max pages to scrape (default 5, max 10)
- `output` — file path to save results

## Tool
`tools/search_and_scrape.py`

## Steps

### Step 1 — Run the search scraper
```bash
python tools/search_and_scrape.py \
  --query "<business name + city if known>" \
  --type contact \
  --limit 5 \
  [--output .tmp/<name>.txt]
```

### Step 2 — Evaluate output
The tool will show:
- Quick hits from search snippets (fastest, no page fetch)
- Full scrape results from top pages
- Merged summary with sources count

### Step 3 — If results are thin, drill into the official site
1. Find the official domain from the results
2. Run `scrape_single_site.py` on the `/contact` or `/about` page:
   ```bash
   python tools/scrape_single_site.py --url "https://example.com/contact"
   ```

### Step 4 — Cross-verify and assign confidence
| Confidence | Condition |
|------------|-----------|
| High       | Found on official site + 1 independent source |
| Medium     | Only official site, OR 2+ directories but no official site |
| Low        | Single non-official source |

### Step 5 — Present concise results
```
[Business Name]
  Phone:   (xxx) xxx-xxxx  (high)
  Email:   info@example.com  (medium)
  Address: 123 Main St, City, ST 12345  (high)
  Web:     https://example.com
  Sources: 4 pages checked
    • https://example.com/contact
    • https://yellowpages.com/…
```

## Edge Cases
- **Chain businesses**: Clarify which location. Re-run with more specific query.
- **No results**: Try alternate name spellings or add state/country to query.
- **Conflicting numbers**: Show both with their source URLs and flag the conflict.
- **Phone-only lookup** (user gives a number, wants to know who owns it): Use the contact-finder skill with reverse-lookup searches.

## Step 5 — Write Summary File (Required)

After all searches are complete, always create a human-readable summary file:

**Filename:** `.tmp/<BusinessName>_<context>_summary.txt`
(e.g., `Acme_Corp_Denver_CO_summary.txt`)

**Contents:**
```
================================================================
CONTACT SEARCH SUMMARY: [Business/Org Name] — [Location]
Date: YYYY-MM-DD
================================================================

[BUSINESS NAME]
  Legal name / DBA / aliases

[ADDRESS]
  Full address — confidence

[PHONE NUMBERS]
  (xxx) xxx-xxxx — confidence

[EMAIL]
  address — confidence (or "not found")

[WEBSITE]
  URL

[HOURS]
  If found

[NOTES]
  Conflicts, caveats, alternate locations

[SOURCES CHECKED]
  List all URLs and search queries used

================================================================
END OF REPORT
================================================================
```

This summary file is **always created**, even when results are sparse. Note what was searched and not found.

## Raw Output Format
Raw per-search results auto-saved to `.tmp/contact_<query>_<timestamp>.txt`
