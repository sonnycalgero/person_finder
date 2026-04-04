# Workflow: Find Individual / Person Information

## Objective
Find publicly available information about an individual: contact details, professional info,
and social media profiles. Cross-verify, group by likely individual, and report confidence.

## Required Inputs
One of:
- `name` — full name (e.g., `"Jane Smith"`)
- `email` — email address (e.g., `"jsmith@company.com"`)
- `username` — social handle (e.g., `"jsmith_dev"`)

Additional context improves accuracy:
- City / state
- Employer or industry
- LinkedIn URL if known

## Tool
`tools/search_and_scrape.py`

## Steps

### Step 1 — Run the search scraper + social media
```bash
# Full search (directories + social media combined):
python tools/search_and_scrape.py \
  --query "<name or email or username> [city] [employer]" \
  --type person \
  --limit 5 \
  [--output .tmp/<name>.txt]

# Social media only (faster, targeted):
python tools/search_social_media.py \
  --query "<full name>" \
  --context "<city state>" \
  --platforms instagram tiktok facebook twitter linkedin github youtube reddit

# Username search (when you have a known handle):
python tools/search_social_media.py \
  --query "<username>" \
  --mode username \
  --platforms all
```

### Step 2 — Follow social profile links
If the scraper finds LinkedIn, Twitter/X, GitHub, etc.:
```bash
python tools/scrape_single_site.py --url "<profile URL>"
```

### Step 3 — For email reverse-lookup
Search for the exact quoted email string:
```bash
python tools/search_and_scrape.py --query '"jsmith@company.com"' --type person
```
Also search the username part separately:
```bash
python tools/search_and_scrape.py --query '"jsmith" site:linkedin.com OR site:github.com' --type person
```

### Step 4 — Group by likely individual
When multiple people share a name, group them by:
- Consistent location across profiles
- Same employer mentioned in multiple sources
- Cross-linked social accounts
- Consistent username pattern

Label groups clearly:
```
[Jane Smith — Group A: likely Austin, TX / Software Engineer]
[Jane Smith — Group B: likely Chicago, IL / Attorney]
[Ungrouped] — profiles that couldn't be assigned
```

### Step 5 — Present concise results
```
[Jane Smith — Group A (Austin, TX)]
  Email:   jane@example.com  (medium)
  Phone:   (512) 555-0100  (low)
  Title:   Senior Engineer at Acme Corp  (high)
  Social:
    LinkedIn     https://linkedin.com/in/janesmith
    GitHub       https://github.com/janesmith
    Twitter/X    https://twitter.com/janesmith_dev
  Sources: 3 pages checked

[Jane Smith — Group B (Chicago, IL)]
  Title:   Associate at Smith & Jones Law  (high)
  Social:
    LinkedIn     https://linkedin.com/in/janesmithlaw
  Sources: 1 page checked
```

## Privacy Guidelines
- Report **publicly available** information only
- Do not attempt to access private/restricted profiles
- Note when a profile appears inactive or info may be outdated
- If only one source and it's not authoritative, label as low confidence

## Edge Cases
- **Very common names**: Run multiple search rounds with employer/city as context. Accept that grouping may be incomplete.
- **Username-only**: Search the username across LinkedIn, GitHub, Twitter, Instagram in separate queries.
- **No results**: Check spelling. Try first name + last initial. Try email username part only.

## Step 6 — Write Summary File (Required)

After all searches are complete, always create a human-readable summary file:

**Filename:** `.tmp/<FirstName>_<LastName>_<context>_summary.txt`
(e.g., `Jane_Smith_Austin_TX_summary.txt`)

**Contents:**
```
================================================================
PERSON SEARCH SUMMARY: [Full Name] — [Location/Context]
Date: YYYY-MM-DD
================================================================

[IDENTITY]
  Full Name / Aliases / Age

[CURRENT LOCATION]
  City, State — confidence level

[PHONE NUMBERS]
  (xxx) xxx-xxxx — confidence

[EMAIL]
  address — confidence (or "not found")

[SOCIAL MEDIA]
  Platform:  handle / URL

[PROFESSIONAL]
  Title, employer, profile URLs

[FAMILY / HOUSEHOLD]
  Related names, shared addresses/phones

[ADDRESSES]
  Full address — confidence

[SOURCES CHECKED]
  List all URLs and search queries used

================================================================
END OF REPORT
================================================================
```

This summary file is **always created**, even when results are sparse. Note what was searched and not found.

## Raw Output Format
Raw per-search results auto-saved to `.tmp/person_<query>_<timestamp>.txt`
