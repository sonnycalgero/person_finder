#!/usr/bin/env python3
"""
scrape_single_site.py — Scrape a single URL and extract contact information.

Usage:
  python scrape_single_site.py --url https://example.com
  python scrape_single_site.py --url https://example.com --output .tmp/results.txt
  python scrape_single_site.py --url https://example.com --json
"""

import argparse
import json
import re
import sys
import os
from datetime import datetime
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: Missing dependencies. Run: pip install requests beautifulsoup4 lxml")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

# Add tools dir to path for shared module
sys.path.insert(0, os.path.dirname(__file__))
from extract_patterns import (
    extract_phones, extract_emails, extract_addresses, extract_from_jsonld
)
from http_client import fetch, _request_with_retry, get_headers

# Domains that reliably require a proxy to return useful data
_PROXY_DOMAINS = {
    "whitepages.com", "spokeo.com", "beenverified.com", "intelius.com",
    "peoplefinder.com", "peoplefinders.com", "mylife.com", "radaris.com",
    "fastpeoplesearch.com", "truepeoplesearch.com", "usphonebook.com",
    "zabasearch.com", "addresses.com", "anywho.com", "yellowpages.com",
    "peekyou.com", "clustrmaps.com", "idcrawl.com",
}


def _needs_proxy(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == d or host.endswith("." + d) for d in _PROXY_DOMAINS)


def _proxy_fetch(url: str) -> tuple[str, str]:
    """Fetch via ScraperAPI or Scrape.do with retry. Called by http_client.fetch()."""
    scraper_api_key = os.environ.get("SCRAPER_API_KEY", "")
    scrape_do_token = os.environ.get("SCRAPE_DO_TOKEN", "")

    # Domains that need JS rendering (costs more credits — use sparingly)
    _JS_DOMAINS = {"radaris.com", "spokeo.com"}
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    needs_js = any(host == d or host.endswith("." + d) for d in _JS_DOMAINS)

    if scraper_api_key:
        params = {
            "api_key": scraper_api_key,
            "url": url,
            "country_code": "us",
        }
        if needs_js:
            params["render"] = "true"
        mode = "proxy+JS" if needs_js else "proxy"
        print(f"  [{mode}: ScraperAPI → {urlparse(url).netloc}]", file=sys.stderr)
        resp = _request_with_retry(
            "http://api.scraperapi.com/", params=params, timeout=60,
        )
        return resp.text, url

    if scrape_do_token:
        params = {
            "token": scrape_do_token,
            "url": url,
            "geoCode": "us",
        }
        if needs_js:
            params["render"] = "true"
        mode = "proxy+JS" if needs_js else "proxy"
        print(f"  [{mode}: Scrape.do → {urlparse(url).netloc}]", file=sys.stderr)
        resp = _request_with_retry(
            "https://api.scrape.do", params=params, timeout=60,
        )
        return resp.text, url

    # No proxy key set
    print(
        f"  [WARNING: {urlparse(url).netloc} needs a proxy but no key is set. "
        "Set SCRAPER_API_KEY or SCRAPE_DO_TOKEN in .env]",
        file=sys.stderr,
    )
    raise RuntimeError(f"No proxy key set for {urlparse(url).netloc}")


def fetch_page(url: str) -> tuple[str, str]:
    """Returns (html_text, final_url). Routes through proxy, requests, or
    Playwright depending on domain and content quality."""
    result = fetch(
        url,
        use_proxy_fn=_needs_proxy,
        proxy_fetch_fn=_proxy_fetch,
    )
    if result.content_diagnosis.is_thin:
        print(
            f"  [WARNING: thin content — {result.content_diagnosis.detail}]",
            file=sys.stderr,
        )
    return result.html, result.final_url


def scrape(url: str) -> dict:
    """Fetch url and return structured contact data."""
    result = {
        "url": url,
        "names": [],
        "phones": [],
        "faxes": [],
        "emails": [],
        "addresses": [],
        "urls": [],
        "social": [],
        "error": None,
    }

    try:
        html, final_url = fetch_page(url)
        result["url"] = final_url
    except Exception as e:
        result["error"] = str(e)
        return result

    soup = BeautifulSoup(html, "html.parser")

    # ── 1. JSON-LD (most reliable) ─────────────────────────────────────────
    jsonld_scripts = [
        s.string for s in soup.find_all("script", type="application/ld+json")
        if s.string
    ]
    if jsonld_scripts:
        structured = extract_from_jsonld(jsonld_scripts)
        for k in ("phones", "faxes", "emails", "addresses", "names", "urls"):
            result[k].extend(structured.get(k, []))

    # ── 2. Meta tags ───────────────────────────────────────────────────────
    og_title = soup.find("meta", property="og:site_name") or soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        name = og_title["content"].strip()
        if name and name not in result["names"]:
            result["names"].append(name)

    # ── 3. <title> as fallback name ────────────────────────────────────────
    if not result["names"] and soup.title and soup.title.string:
        t = soup.title.string.strip().split("|")[0].split("–")[0].split("-")[0].strip()
        if t:
            result["names"].append(t)

    # ── 4. mailto: links ───────────────────────────────────────────────────
    for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
        email = a["href"][7:].split("?")[0].strip().lower()
        if email and email not in result["emails"]:
            result["emails"].append(email)

    # ── 5. tel: links ──────────────────────────────────────────────────────
    for a in soup.find_all("a", href=re.compile(r"^tel:", re.I)):
        raw = a["href"][4:].strip()
        phones = extract_phones(raw) or [raw]
        for p in phones:
            if p not in result["phones"]:
                result["phones"].append(p)

    # ── 6. Social media links ──────────────────────────────────────────────
    _social_domains = {
        "linkedin.com": "LinkedIn",
        "twitter.com": "Twitter/X",
        "x.com": "Twitter/X",
        "facebook.com": "Facebook",
        "instagram.com": "Instagram",
        "github.com": "GitHub",
        "youtube.com": "YouTube",
        "tiktok.com": "TikTok",
    }
    _current_host = urlparse(final_url).netloc
    seen_social = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Build the absolute URL and check the resolved host matches a social domain
        abs_href = urljoin(final_url, href)
        resolved_host = urlparse(abs_href).netloc
        for domain, label in _social_domains.items():
            if domain in resolved_host and abs_href not in seen_social:
                # Skip the page's own generic social links (share buttons etc.)
                path = urlparse(abs_href).path
                if any(skip in path for skip in ("/sharer", "/share", "/intent/tweet", "/dialog/")):
                    break
                result["social"].append({"platform": label, "url": abs_href})
                seen_social.add(abs_href)
                break

    # ── 7. Regex on visible text (fallback) ────────────────────────────────
    # Remove script/style noise
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")

    for p in extract_phones(text):
        if p not in result["phones"]:
            result["phones"].append(p)

    for e in extract_emails(text):
        if e not in result["emails"]:
            result["emails"].append(e)

    for a in extract_addresses(text):
        if a not in result["addresses"]:
            result["addresses"].append(a)

    # ── 8. Deduplicate and trim ────────────────────────────────────────────
    result["names"] = list(dict.fromkeys(result["names"]))[:3]
    result["phones"] = list(dict.fromkeys(result["phones"]))[:5]
    result["emails"] = list(dict.fromkeys(result["emails"]))[:5]
    result["addresses"] = list(dict.fromkeys(result["addresses"]))[:3]
    result["social"] = result["social"][:6]

    return result


def format_result(r: dict, source_label: str = "") -> str:
    """Return a concise, human-readable string for one result."""
    lines = []
    label = source_label or r.get("url", "")
    name = r["names"][0] if r["names"] else ""
    header = name if name else label
    lines.append(f"[{header}]")
    if name and label and name != label:
        lines.append(f"  URL:     {label}")

    if r.get("error"):
        lines.append(f"  ERROR:   {r['error']}")
        return "\n".join(lines)

    if r["phones"]:
        lines.append(f"  Phone:   {' | '.join(r['phones'])}")
    if r["faxes"]:
        lines.append(f"  Fax:     {' | '.join(r['faxes'])}")
    if r["emails"]:
        lines.append(f"  Email:   {' | '.join(r['emails'])}")
    if r["addresses"]:
        lines.append(f"  Address: {r['addresses'][0]}")
        for a in r["addresses"][1:]:
            lines.append(f"           {a}")
    if r["social"]:
        for s in r["social"]:
            lines.append(f"  {s['platform']:12} {s['url']}")

    if not any([r["phones"], r["emails"], r["addresses"], r["social"]]):
        lines.append("  (no contact info found)")

    return "\n".join(lines)


def save_to_file(content: str, output_path: str) -> None:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(content + "\n")


def _print_help():
    print("""
scrape_single_site.py — Scrape a single URL and extract contact information
════════════════════════════════════════════════════════════════════════════

USAGE
  python scrape_single_site.py --url "https://..." [options]

ARGUMENTS

  --url URL         (required)
      The full URL of the page to scrape.
      Example: "https://www.example.com/contact"

  --output FILE
      Append extracted results to this file path. If omitted, output is
      printed to the terminal only (not auto-saved).

  --json
      Output raw JSON instead of formatted text. Useful for piping into
      other tools or scripts.

WHAT IT EXTRACTS
  - Phone numbers (US and international formats)
  - Fax numbers
  - Email addresses
  - Physical addresses
  - Social media profile links (LinkedIn, Twitter, Instagram, etc.)
  - Business/organization name (from JSON-LD, og:title, or <title>)

HOW IT WORKS
  1. Fetches the page (uses ScraperAPI proxy if SCRAPER_API_KEY is set
     and the domain is a known people-finder site)
  2. Parses JSON-LD structured data (most reliable)
  3. Scans meta tags, mailto: and tel: links
  4. Finds social media links in the page
  5. Falls back to regex extraction on visible text

EXAMPLES

  # Scrape a contact page
  python scrape_single_site.py --url "https://www.acmecorp.com/contact"

  # Scrape and save results to a file
  python scrape_single_site.py --url "https://www.acmecorp.com/contact" --output .tmp/acme.txt

  # Get raw JSON output
  python scrape_single_site.py --url "https://www.acmecorp.com/contact" --json

NOTE
  For people-finder sites (Whitepages, Spokeo, Radaris, etc.), set a
  scraping proxy key in .env to bypass blocks:
    SCRAPER_API_KEY  — ScraperAPI (takes priority if both are set)
    SCRAPE_DO_TOKEN  — Scrape.do (used when ScraperAPI key is absent)
  Without either key, these sites will likely return empty results.
""")


def main():
    if "/help" in sys.argv:
        _print_help()
        sys.exit(0)

    parser = argparse.ArgumentParser(description="Scrape a URL for contact info")
    parser.add_argument("--url", required=True, help="URL to scrape")
    parser.add_argument("--output", default="", help="Append results to this file")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    result = scrape(args.url)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    header = (
        f"=== scrape_single_site | {args.url} | {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n"
    )
    body = format_result(result)
    output = header + body + "\n"

    print(output)

    if args.output:
        save_to_file(output, args.output)
        print(f"[saved → {args.output}]")


if __name__ == "__main__":
    main()
