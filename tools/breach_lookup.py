#!/usr/bin/env python3
"""
breach_lookup.py — Check an email address across multiple breach-notification
services and produce a consolidated summary.

Services queried:
  • HaveIBeenPwned (HIBP)    — requires HIBP_API_KEY (paid)
  • XposedOrNot              — free, no key required
  • BreachDirectory          — requires RAPIDAPI_KEY (free tier on RapidAPI)
  • Norton/LifeLock          — no public API; emits a manual-check URL

Usage:
  python breach_lookup.py --email "someone@example.com"
  python breach_lookup.py --email "someone@example.com" --json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

import requests

sys.path.insert(0, os.path.dirname(__file__))

HELP_TEXT = """\
breach_lookup.py — Check an email against multiple breach-notification services
═══════════════════════════════════════════════════════════════════════════════

USAGE
  python breach_lookup.py --email "user@example.com" [--json] [--output FILE]

SERVICES QUERIED
  • HaveIBeenPwned    (requires HIBP_API_KEY in .env — paid, ~$3.95/mo)
  • XposedOrNot       (free, no key)
  • BreachDirectory   (requires RAPIDAPI_KEY in .env — free tier available)
  • Norton/LifeLock   (no public API — emits manual-check URL)

NOTES
  • Services without API keys are skipped with a warning.
  • Results are auto-saved to .tmp/breach_<email>_<timestamp>.txt
  • Use --json for raw output suitable for piping.
"""

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

UA = "Mozilla/5.0 breach-lookup/1.0 (+python-requests)"
TIMEOUT = 20


# ── HaveIBeenPwned ───────────────────────────────────────────────────────────

def query_hibp(email: str) -> dict:
    """Query HIBP breachedaccount endpoint. Requires HIBP_API_KEY."""
    key = os.environ.get("HIBP_API_KEY", "").strip()
    if not key:
        return {"service": "HaveIBeenPwned", "status": "skipped",
                "reason": "HIBP_API_KEY not set in .env"}

    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{requests.utils.quote(email)}"
    headers = {
        "hibp-api-key": key,
        "user-agent":   UA,
        "Accept":       "application/json",
    }
    params = {"truncateResponse": "false"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
    except Exception as e:
        return {"service": "HaveIBeenPwned", "status": "error", "reason": str(e)}

    if r.status_code == 404:
        return {"service": "HaveIBeenPwned", "status": "clean", "breaches": []}
    if r.status_code == 401:
        return {"service": "HaveIBeenPwned", "status": "error",
                "reason": "unauthorized — check HIBP_API_KEY"}
    if r.status_code == 429:
        return {"service": "HaveIBeenPwned", "status": "error", "reason": "rate limited"}
    if not r.ok:
        return {"service": "HaveIBeenPwned", "status": "error",
                "reason": f"HTTP {r.status_code}: {r.text[:120]}"}

    try:
        breaches = r.json()
    except ValueError:
        return {"service": "HaveIBeenPwned", "status": "error", "reason": "invalid JSON"}

    return {
        "service":  "HaveIBeenPwned",
        "status":   "found" if breaches else "clean",
        "breaches": [
            {
                "name":        b.get("Name"),
                "title":       b.get("Title"),
                "domain":      b.get("Domain"),
                "breach_date": b.get("BreachDate"),
                "added_date":  b.get("AddedDate"),
                "pwn_count":   b.get("PwnCount"),
                "data_classes": b.get("DataClasses", []),
                "description": _strip_html(b.get("Description", ""))[:400],
            }
            for b in breaches
        ],
    }


# ── XposedOrNot ──────────────────────────────────────────────────────────────

def query_xposedornot(email: str) -> dict:
    """Query XposedOrNot breach-analytics endpoint. Free, no key."""
    url = f"https://api.xposedornot.com/v1/breach-analytics"
    try:
        r = requests.get(url, params={"email": email},
                         headers={"user-agent": UA}, timeout=TIMEOUT)
    except Exception as e:
        return {"service": "XposedOrNot", "status": "error", "reason": str(e)}

    if r.status_code == 404:
        return {"service": "XposedOrNot", "status": "clean", "breaches": []}
    if not r.ok:
        return {"service": "XposedOrNot", "status": "error",
                "reason": f"HTTP {r.status_code}: {r.text[:120]}"}

    try:
        data = r.json()
    except ValueError:
        return {"service": "XposedOrNot", "status": "error", "reason": "invalid JSON"}

    # XposedOrNot returns either {"Error": "Not found"} or a rich analytics payload
    if isinstance(data, dict) and data.get("Error"):
        return {"service": "XposedOrNot", "status": "clean", "breaches": []}

    exposed = data.get("ExposedBreaches", {}).get("breaches_details", []) or []
    summary = data.get("BreachesSummary", {}) or {}
    metrics = data.get("BreachMetrics", {}) or {}

    return {
        "service":  "XposedOrNot",
        "status":   "found" if exposed else "clean",
        "summary":  {
            "site":              summary.get("site"),
            "industries":        metrics.get("industry"),
            "exposure_score":    (metrics.get("risk") or [{}])[0].get("risk_score")
                                 if metrics.get("risk") else None,
            "password_strength": metrics.get("passwords_strength"),
        },
        "breaches": [
            {
                "name":         b.get("breach"),
                "domain":       b.get("domain"),
                "breach_date":  b.get("xposed_date"),
                "pwn_count":    b.get("xposed_records"),
                "data_classes": (b.get("xposed_data") or "").split(";"),
                "description":  (b.get("details") or "")[:400],
            }
            for b in exposed
        ],
    }


# ── BreachDirectory (RapidAPI) ───────────────────────────────────────────────

def query_breachdirectory(email: str) -> dict:
    """Query BreachDirectory via RapidAPI. Requires RAPIDAPI_KEY."""
    key = os.environ.get("RAPIDAPI_KEY", "").strip()
    if not key:
        return {"service": "BreachDirectory", "status": "skipped",
                "reason": "RAPIDAPI_KEY not set in .env"}

    url = "https://breachdirectory.p.rapidapi.com/"
    headers = {
        "x-rapidapi-key":  key,
        "x-rapidapi-host": "breachdirectory.p.rapidapi.com",
        "user-agent":      UA,
    }
    params = {"func": "auto", "term": email}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
    except Exception as e:
        return {"service": "BreachDirectory", "status": "error", "reason": str(e)}

    if r.status_code in (401, 403):
        return {"service": "BreachDirectory", "status": "error",
                "reason": "unauthorized — check RAPIDAPI_KEY subscription to BreachDirectory"}
    if r.status_code == 429:
        return {"service": "BreachDirectory", "status": "error", "reason": "rate limited"}
    if not r.ok:
        return {"service": "BreachDirectory", "status": "error",
                "reason": f"HTTP {r.status_code}: {r.text[:120]}"}

    try:
        data = r.json()
    except ValueError:
        return {"service": "BreachDirectory", "status": "error", "reason": "invalid JSON"}

    if not data.get("success"):
        return {"service": "BreachDirectory", "status": "error",
                "reason": data.get("error") or "api returned success=false"}

    entries = data.get("result") or []
    return {
        "service":  "BreachDirectory",
        "status":   "found" if entries else "clean",
        "count":    data.get("found", len(entries)),
        "breaches": [
            {
                "source":   e.get("sources") or e.get("source"),
                "line":     e.get("line"),
                "password": e.get("password"),
                "sha1":     e.get("sha1"),
                "hash":     e.get("hash"),
            }
            for e in entries
        ],
    }


# ── Norton/LifeLock (no public API) ──────────────────────────────────────────

def query_norton(email: str) -> dict:
    """Norton has no public breach-check API. Return a manual-check URL."""
    return {
        "service": "Norton/LifeLock",
        "status":  "manual",
        "reason":  "no public API — visit the URL below to check manually",
        "url":     "https://lifelock.norton.com/breach-detection",
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").replace("&quot;", '"').replace("&amp;", "&").strip()


def _format_breach_line(b: dict) -> str:
    parts = []
    if b.get("name") or b.get("title"):
        parts.append(f"{b.get('title') or b.get('name')}")
    if b.get("breach_date"):
        parts.append(f"({b['breach_date']})")
    if b.get("pwn_count"):
        parts.append(f"— {b['pwn_count']:,} accounts" if isinstance(b["pwn_count"], int)
                     else f"— {b['pwn_count']} accounts")
    return " ".join(parts)


def format_results(email: str, results: list[dict]) -> str:
    width = 72
    lines = []
    lines.append("═" * width)
    lines.append(f"  BREACH LOOKUP SUMMARY — {email}")
    lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("═" * width)

    # Aggregate count of distinct breach names
    all_breach_names = set()
    total_hits = 0
    for res in results:
        if res["status"] == "found":
            for b in res.get("breaches", []):
                if b.get("name") or b.get("title") or b.get("source"):
                    all_breach_names.add(str(b.get("name") or b.get("title") or b.get("source")))
                total_hits += 1

    lines.append(f"\n  Unique breach names: {len(all_breach_names)}")
    lines.append(f"  Total hits across services: {total_hits}")

    for res in results:
        lines.append("\n" + "─" * width)
        lines.append(f"  [{res['service']}] — {res['status'].upper()}")
        lines.append("─" * width)

        status = res["status"]
        if status == "skipped":
            lines.append(f"    Skipped: {res.get('reason')}")
            continue
        if status == "error":
            lines.append(f"    Error: {res.get('reason')}")
            continue
        if status == "manual":
            lines.append(f"    {res.get('reason')}")
            lines.append(f"    URL: {res.get('url')}")
            continue
        if status == "clean":
            lines.append("    No breaches found for this email.")
            continue

        # status == found
        if "summary" in res and res["summary"]:
            s = res["summary"]
            if s.get("exposure_score") is not None:
                lines.append(f"    Exposure score:    {s['exposure_score']}")
            if s.get("password_strength"):
                lines.append(f"    Password strength: {s['password_strength']}")
            if s.get("industries"):
                lines.append(f"    Industries:        {s['industries']}")
        if "count" in res:
            lines.append(f"    Total records:     {res['count']}")

        breaches = res.get("breaches", [])
        lines.append(f"    Breaches: {len(breaches)}")
        for b in breaches[:25]:
            header = _format_breach_line(b)
            if header:
                lines.append(f"      • {header}")
            if b.get("domain"):
                lines.append(f"          domain:  {b['domain']}")
            if b.get("data_classes"):
                dc = b["data_classes"] if isinstance(b["data_classes"], list) else [b["data_classes"]]
                dc = [x for x in dc if x]
                if dc:
                    lines.append(f"          exposed: {', '.join(dc)}")
            if b.get("password"):
                lines.append(f"          password: {b['password']}")
            if b.get("sha1"):
                lines.append(f"          sha1:    {b['sha1']}")
            if b.get("source"):
                lines.append(f"          source:  {b['source']}")
            if b.get("description"):
                lines.append(f"          note:    {b['description']}")
        if len(breaches) > 25:
            lines.append(f"      ... and {len(breaches) - 25} more (see --json for full list)")

    lines.append("\n" + "═" * width)
    lines.append("  END OF REPORT")
    lines.append("═" * width)
    return "\n".join(lines)


# ── File output ──────────────────────────────────────────────────────────────

def _auto_output_path(email: str) -> str:
    base_dir = os.path.join(os.path.dirname(__file__), "..", ".tmp")
    os.makedirs(base_dir, exist_ok=True)
    slug = re.sub(r"[^\w\-]", "_", email)[:60]
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.abspath(os.path.join(base_dir, f"breach_{slug}_{ts}.txt"))


def _save_output(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content + "\n")
    print(f"[auto-saved → {path}]")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] in ("/help", "--help", "-h", "help"):
        print(HELP_TEXT)
        return

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--email",  required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--json",   action="store_true", dest="json_out")
    args = parser.parse_args()

    email = args.email.strip().lower()
    if not EMAIL_RE.match(email):
        print(f"ERROR: '{email}' is not a valid email address", file=sys.stderr)
        sys.exit(1)

    services = [
        ("HaveIBeenPwned",   query_hibp),
        ("XposedOrNot",      query_xposedornot),
        ("BreachDirectory",  query_breachdirectory),
        ("Norton/LifeLock",  query_norton),
    ]

    results = []
    for name, fn in services:
        print(f"[querying {name}...]", file=sys.stderr)
        try:
            results.append(fn(email))
        except Exception as e:
            results.append({"service": name, "status": "error", "reason": f"unhandled: {e}"})

    if args.json_out:
        output = json.dumps({"email": email, "results": results}, indent=2, default=str)
    else:
        output = format_results(email, results)

    print(output)

    out_path = args.output or _auto_output_path(email)
    _save_output(out_path, output)


if __name__ == "__main__":
    main()
