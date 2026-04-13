#!/usr/bin/env python3
"""
search_and_scrape.py — Search the web for contact or person info, then scrape top results.

Usage:
  python search_and_scrape.py --query "Acme Corp Denver CO" --type contact
  python search_and_scrape.py --query "John Smith software engineer Austin" --type person
  python search_and_scrape.py --query "john@example.com" --type person --limit 5
  python search_and_scrape.py --query "Acme Corp" --type contact --output .tmp/acme.txt
"""

import argparse
import json
import os
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        print("ERROR: Missing ddgs. Run: pip install ddgs")
        sys.exit(1)

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: Missing dependencies. Run: pip install requests beautifulsoup4 lxml")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(__file__))
from extract_patterns import extract_phones, extract_emails, extract_addresses
from scrape_single_site import scrape, format_result
from search_social_media import search_all_platforms, format_results as format_social, DEFAULT_PLATFORMS

DEFAULT_LIMIT = 5
MAX_LIMIT = 10

# Search query templates per type
CONTACT_QUERIES = [
    "{query} phone number address",
    "{query} contact information",
    "{query} official website",
]

PERSON_QUERIES = [
    '"{query}" LinkedIn',
    '"{query}" contact email',
    '"{query}" site:twitter.com OR site:instagram.com OR site:facebook.com',
    '"{query}" site:github.com OR site:linkedin.com',
    '"{query}" whitepages OR spokeo OR peoplefinder',
]

# Skip these domains — they rarely have useful raw contact data
SKIP_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "tiktok.com", "youtube.com", "pinterest.com",
    "yelp.com",  # keep yellowpages
}


def should_skip(url: str) -> bool:
    for d in SKIP_DOMAINS:
        if d in url:
            return True
    return False


def _build_directory_urls(first: str, last: str, state: str = "") -> list:
    """
    Construct direct URLs for people-finder directory sites.
    These bypass DDG search entirely — we go straight to the source.
    """
    slug = f"{first}-{last}".lower()
    slug_under = f"{first}_{last}".lower()
    state_up = state.upper() if state else ""

    urls = [
        f"https://www.whitepages.com/name/{first.capitalize()}-{last.capitalize()}" + (f"/{state_up}" if state_up else ""),
        f"https://www.truepeoplesearch.com/results?name={first}%20{last}" + (f"&citystatezip={state_up}" if state_up else ""),
        f"https://www.fastpeoplesearch.com/name/{slug}" + (f"_{state.lower()}" if state else ""),
        f"https://radaris.com/p/{first.capitalize()}/{last.capitalize()}/",
        f"https://www.spokeo.com/{first.capitalize()}-{last.capitalize()}" + (f"/{state_up}" if state_up else ""),
        f"https://www.peekyou.com/{slug_under}",
    ]
    return urls


def _parse_name_and_state(query: str) -> tuple[str, str, str, str]:
    """
    Best-effort parse of 'First Last City ST' style query.
    Returns (first, last, city, state).
    """
    import re
    # Match a trailing 2-letter state abbreviation
    state_match = re.search(r'\b([A-Z]{2})\b', query)
    state = state_match.group(1) if state_match else ""

    # Strip state and city (anything after the 2nd word) to get name
    parts = query.strip().split()
    first = parts[0] if len(parts) >= 1 else ""
    last = parts[1] if len(parts) >= 2 else ""
    city = " ".join(parts[2:-1]) if len(parts) > 3 else (parts[2] if len(parts) == 3 and not state else "")

    return first, last, city, state


def ddg_search(query: str, max_results: int = 10) -> list:
    """Return list of {title, url, body} dicts from DuckDuckGo."""
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
    except Exception as e:
        print(f"  [search error: {e}]", file=sys.stderr)
    return results


def collect_urls(search_type: str, query: str, limit: int) -> list:
    """Run multiple queries and collect unique URLs to scrape."""
    templates = CONTACT_QUERIES if search_type == "contact" else PERSON_QUERIES
    seen_urls = set()
    all_results = []

    # For person searches, inject direct directory URLs at the front if ScraperAPI is set
    if search_type == "person" and os.environ.get("SCRAPER_API_KEY"):
        first, last, city, state = _parse_name_and_state(query)
        if first and last:
            dir_urls = _build_directory_urls(first, last, state)
            print(f"  [directories: {len(dir_urls)} direct URLs queued]", file=sys.stderr)
            for url in dir_urls:
                if url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append({"title": "", "url": url, "snippet": ""})

    for tmpl in templates:
        q = tmpl.format(query=query)
        hits = ddg_search(q, max_results=limit + 3)
        for h in hits:
            url = h["url"]
            if url and url not in seen_urls and not should_skip(url):
                seen_urls.add(url)
                all_results.append(h)
            if len(all_results) >= limit * 3:
                break
        if len(all_results) >= limit * 3:
            break

    # Also extract contact info from search snippets directly
    snippet_contacts = _contacts_from_snippets(all_results)

    _dir_domains = {"whitepages.com", "spokeo.com", "truepeoplesearch.com",
                    "fastpeoplesearch.com", "radaris.com", "peekyou.com"}
    dir_hits = [r for r in all_results if any(d in r["url"] for d in _dir_domains)]
    other_hits = [r for r in all_results if r not in dir_hits]
    # Always include all directory hits; fill remaining slots with other results
    combined = dir_hits + other_hits[:max(0, limit - len(dir_hits))]
    return combined, snippet_contacts


def _contacts_from_snippets(hits: list) -> dict:
    """Fast pass: pull phones/emails from search snippets without fetching pages."""
    phones, emails = [], []
    seen_p, seen_e = set(), set()
    for h in hits:
        text = h.get("snippet", "") + " " + h.get("title", "")
        for p in extract_phones(text):
            if p not in seen_p:
                seen_p.add(p)
                phones.append(p)
        for e in extract_emails(text):
            if e not in seen_e:
                seen_e.add(e)
                emails.append(e)
    return {"phones": phones[:5], "emails": emails[:5]}


def merge_results(scraped: list) -> dict:
    """Merge multiple scrape results into one aggregated record."""
    merged = {
        "names": [],
        "phones": [],
        "faxes": [],
        "emails": [],
        "addresses": [],
        "urls": [],
        "social": [],
        "sources": [],
    }
    seen = {k: set() for k in merged}

    for r in scraped:
        if r.get("error"):
            continue
        merged["sources"].append(r.get("url", ""))
        for k in ("names", "phones", "faxes", "emails", "addresses"):
            for v in r.get(k, []):
                if v and v not in seen[k]:
                    seen[k].add(v)
                    merged[k].append(v)
        for s in r.get("social", []):
            key = s["url"]
            if key not in seen["social"]:
                seen["social"].add(key)
                merged["social"].append(s)

    return merged


def format_merged(merged: dict, query: str, search_type: str) -> str:
    """Format merged results concisely."""
    lines = []
    name = merged["names"][0] if merged["names"] else query
    lines.append(f"[{name}]")

    if merged["phones"]:
        lines.append(f"  Phone:   {' | '.join(merged['phones'][:3])}")
    if merged["faxes"]:
        lines.append(f"  Fax:     {' | '.join(merged['faxes'][:2])}")
    if merged["emails"]:
        lines.append(f"  Email:   {' | '.join(merged['emails'][:3])}")
    if merged["addresses"]:
        lines.append(f"  Address: {merged['addresses'][0]}")
        for a in merged["addresses"][1:3]:
            lines.append(f"           {a}")

    if search_type == "person" and merged["social"]:
        lines.append("  Social:")
        for s in merged["social"][:6]:
            lines.append(f"    {s['platform']:12} {s['url']}")

    if merged["sources"]:
        lines.append(f"  Sources: {len(merged['sources'])} pages checked")
        for src in merged["sources"][:3]:
            lines.append(f"    • {src}")

    if not any([merged["phones"], merged["emails"], merged["addresses"], merged["social"]]):
        lines.append("  (no contact info found in scraped pages)")

    return "\n".join(lines)


def save_to_file(content: str, output_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(content + "\n")


def _print_help():
    print("""
search_and_scrape.py — Search the web for person or business contact info
══════════════════════════════════════════════════════════════════════════

USAGE
  python search_and_scrape.py --query "..." [options]

ARGUMENTS

  --query TEXT              (required)
      What to search for. Can be a person's name, username, email address,
      or business name.
      Examples:
        "John Smith software engineer Austin TX"
        "jsmith92"
        "Acme Corp Denver CO"

  --type {contact,person}   (default: contact)
      Search mode:
        contact  — Find phone, address, and email for a business or org
        person   — Find social profiles and contact info for an individual

  --mode {name,username}    (default: name)
      How to interpret the query for social media searches:
        name      — Treat query as a person's full name
        username  — Treat query as a social media handle or username

  --limit N                 (default: 5, max: 10)
      Number of web pages to scrape. Higher = slower but more thorough.

  --output FILE
      Append results to this file path. If omitted, results auto-save
      to .tmp/ with a timestamped filename.

  --json
      Output raw JSON instead of formatted text. Useful for piping into
      other tools or scripts.

  --no-social
      Skip the social media platform search step.
      Only applies when --type is 'person'.

  --social-platforms PLATFORM [PLATFORM ...]
      Which social platforms to search. Defaults to all major platforms.
      Available: instagram tiktok facebook twitter linkedin github youtube
                 reddit pinterest snapchat

EXAMPLES

  # Find contact info for a business
  python search_and_scrape.py --query "Acme Corp Denver CO" --type contact

  # Find a person by full name
  python search_and_scrape.py --query "John Smith Austin TX" --type person

  # Find a person by username/handle across social platforms
  python search_and_scrape.py --query "jsmith92" --type person --mode username

  # Narrow to specific platforms only
  python search_and_scrape.py --query "Jane Doe" --type person --social-platforms instagram tiktok linkedin

  # Save results to a specific file
  python search_and_scrape.py --query "Acme Corp" --type contact --output .tmp/acme.txt

  # Output raw JSON
  python search_and_scrape.py --query "John Smith" --type person --json

  # Scrape more pages for thorough results
  python search_and_scrape.py --query "Jane Doe Boise ID" --type person --limit 10

NOTE
  Results are always auto-saved to .tmp/ even without --output.
  Social media results also generate an HTML report in .tmp/.
""")


def main():
    if "/help" in sys.argv:
        _print_help()
        sys.exit(0)

    parser = argparse.ArgumentParser(description="Search and scrape for contact/person info")
    parser.add_argument("--query", required=True, help="Search query (name, email, username, business)")
    parser.add_argument("--type", choices=["contact", "person"], default="contact",
                        help="Search mode: 'contact' for businesses, 'person' for individuals")
    parser.add_argument("--mode", choices=["name", "username"], default="name",
                        help="How to interpret the query for social search: 'name' or 'username'")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Max pages to scrape (default {DEFAULT_LIMIT}, max {MAX_LIMIT})")
    parser.add_argument("--output", default="", help="Append results to this file")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--no-social", action="store_true",
                        help="Skip social media search (person searches only)")
    parser.add_argument("--social-platforms", nargs="+",
                        default=DEFAULT_PLATFORMS,
                        help="Social platforms to search (default: all major platforms)")
    args = parser.parse_args()

    limit = min(args.limit, MAX_LIMIT)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"Searching ({args.type}): {args.query!r}  [{ts}]")
    print(f"Fetching up to {limit} pages…\n")

    hits, snippet_contacts = collect_urls(args.type, args.query, limit)

    if not hits:
        print("No search results found.")
        return

    # Show snippet-level finds first (no page fetch needed)
    if snippet_contacts["phones"] or snippet_contacts["emails"]:
        print("[Quick hits from search snippets]")
        if snippet_contacts["phones"]:
            print(f"  Phone:  {' | '.join(snippet_contacts['phones'])}")
        if snippet_contacts["emails"]:
            print(f"  Email:  {' | '.join(snippet_contacts['emails'])}")
        print()

    # Scrape pages
    scraped = []
    for i, hit in enumerate(hits[:limit], 1):
        url = hit["url"]
        print(f"  [{i}/{limit}] {url}")
        r = scrape(url)
        scraped.append(r)

    print()

    # Merge and display
    merged = merge_results(scraped)

    # Patch in snippet contacts not found in pages
    for p in snippet_contacts["phones"]:
        if p not in merged["phones"]:
            merged["phones"].insert(0, p)
    for e in snippet_contacts["emails"]:
        if e not in merged["emails"]:
            merged["emails"].insert(0, e)

    # ── Social media search (person mode only) ────────────────────────────
    social_results = []
    social_body = ""
    if args.type == "person" and not args.no_social:
        print("── Social Media ──────────────────────────────────────")
        social_results = search_all_platforms(
            name=args.query,
            platforms=args.social_platforms,
            mode=args.mode,
        )
        social_body = format_social(social_results, args.query)
        print(social_body)
        print()

    if args.json:
        print(json.dumps({"query": args.query, "type": args.type, "results": merged,
                          "social": social_results}, indent=2))
        return

    header = f"=== {args.type.upper()} SEARCH: {args.query!r} | {ts} ===\n"
    body = format_merged(merged, args.query, args.type)
    if social_body:
        body += "\n\nSOCIAL MEDIA:\n" + social_body
    output = header + body + "\n"

    print(output)

    if args.output:
        save_to_file(output, args.output)
        print(f"[saved → {args.output}]")
    else:
        # Auto-save to .tmp/
        script_dir = os.path.dirname(os.path.abspath(__file__))
        tmp_dir = os.path.join(script_dir, "..", ".tmp")
        safe_query = "".join(c if c.isalnum() or c in "-_ " else "_" for c in args.query)[:40]
        ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
        auto_path = os.path.join(tmp_dir, f"{args.type}_{safe_query}_{ts_file}.txt")
        save_to_file(output, auto_path)
        print(f"[auto-saved → {auto_path}]")


if __name__ == "__main__":
    main()
