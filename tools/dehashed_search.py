#!/usr/bin/env python3
"""
dehashed_search.py — Query the DeHashed API for breach/leak data by name, email,
username, IP address, physical address, phone number, VIN, or domain.

Usage:
  python dehashed_search.py --query "john@example.com"
  python dehashed_search.py --query "John Smith" --field name
  python dehashed_search.py --query "jsmith92" --field username
  python dehashed_search.py --query "example.com" --field domain
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

# ── Constants ─────────────────────────────────────────────────────────────────

DEHASHED_URL = "https://api.dehashed.com/v2/search"

FIELDS = {
    "name":       "name",
    "email":      "email",
    "username":   "username",
    "ip":         "ip_address",
    "ip_address": "ip_address",
    "address":    "address",
    "phone":      "phone",
    "vin":        "vin",
    "domain":     "domain",
}

# Fields to display in output (and their labels)
DISPLAY_FIELDS = [
    ("name",            "Name"),
    ("email",           "Email"),
    ("username",        "Username"),
    ("ip_address",      "IP Address"),
    ("address",         "Address"),
    ("phone",           "Phone"),
    ("vin",             "VIN"),
    ("password",        "Password"),
    ("hashed_password", "Hash"),
    ("hash_type",       "Hash Type"),
    ("database_name",   "Source DB"),
]

DEFAULT_SIZE = 10
MAX_SIZE = 10000

HELP_TEXT = """\
dehashed_search.py — Query the DeHashed API for breach/leak data
══════════════════════════════════════════════════════════════════

REQUIRES
  DEHASHED_EMAIL and DEHASHED_API_KEY set in .env

USAGE
  python dehashed_search.py --query "..." [options]

ARGUMENTS

  --query TEXT              (required)
      The value to search for. Field type is auto-detected when
      --field is omitted.
      Examples:
        "john@example.com"          → auto-detected as email
        "John Smith"                → auto-detected as name
        "jsmith92"                  → auto-detected as username
        "192.168.1.1"               → auto-detected as IP
        "example.com"               → auto-detected as domain
        "1HGCM82633A004352"         → auto-detected as VIN
        "(555) 867-5309"            → auto-detected as phone

  --field FIELD             (default: auto-detect)
      Force a specific field type. Choices:
        name       — person's full or partial name
        email      — email address
        username   — social/login handle
        ip         — IPv4 or IPv6 address
        address    — physical street address
        phone      — phone number (any format)
        vin        — vehicle identification number
        domain     — email domain (e.g. example.com)

  --size N                  (default: 10, max: 10000)
      Number of results to return per page.

  --page N                  (default: 1)
      Page number for pagination.

  --output FILE
      Append results to this file. If omitted, auto-saves to .tmp/.

  --json
      Output raw JSON instead of formatted text.

EXAMPLES

  # Search by email (auto-detected)
  python dehashed_search.py --query "john@example.com"

  # Search by name with more results
  python dehashed_search.py --query "John Smith" --size 25

  # Search a domain for all breach entries
  python dehashed_search.py --query "example.com" --field domain --size 50

  # Search by username
  python dehashed_search.py --query "jsmith92" --field username

  # Get raw JSON
  python dehashed_search.py --query "john@example.com" --json

  # Paginate through results
  python dehashed_search.py --query "example.com" --field domain --size 10 --page 2

NOTE
  Results are always auto-saved to .tmp/ even without --output.
  DeHashed charges credits per query — check your balance at dehashed.com.
"""


# ── Field auto-detection ──────────────────────────────────────────────────────

_EMAIL_RE    = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_IPV4_RE     = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_IPV6_RE     = re.compile(r"^[0-9a-fA-F:]+:[0-9a-fA-F:]+$")
_PHONE_RE    = re.compile(r"^[\+\(]?\d[\d\s\-\.\(\)]{6,}\d$")
_VIN_RE      = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)
_DOMAIN_RE   = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}$")


def _autodetect_field(query: str) -> str:
    """Infer the most likely DeHashed field type from the query string."""
    q = query.strip()

    if _EMAIL_RE.match(q):
        return "email"

    if _IPV4_RE.match(q) or _IPV6_RE.match(q):
        return "ip_address"

    # Phone: digit-heavy, no letters
    digits = re.sub(r"\D", "", q)
    if _PHONE_RE.match(q) and 7 <= len(digits) <= 15 and not re.search(r"[a-zA-Z]", q):
        return "phone"

    # VIN: exactly 17 alphanumeric, no I/O/Q
    if _VIN_RE.match(q) and len(q) == 17:
        return "vin"

    # Domain: no spaces, looks like host.tld
    if " " not in q and _DOMAIN_RE.match(q):
        return "domain"

    # Username: no spaces (but not a domain)
    if " " not in q:
        return "username"

    # Default: name
    return "name"


# ── API call ──────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    """Load DeHashed API key from environment. Exit if missing."""
    api_key = os.environ.get("DEHASHED_API_KEY", "").strip()
    if not api_key:
        print(
            "ERROR: DEHASHED_API_KEY must be set in .env\n"
            "  Get your API key at https://dehashed.com/profile",
            file=sys.stderr,
        )
        sys.exit(1)
    return api_key


def _build_query(raw_query: str, field: str) -> str:
    """Build the DeHashed query string with field operator."""
    # Wrap multi-word values in quotes; single tokens don't need them
    value = f'"{raw_query}"' if " " in raw_query else raw_query
    return f"{field}:{value}"


def query_dehashed(
    raw_query: str,
    field: str,
    *,
    size: int = DEFAULT_SIZE,
    page: int = 1,
) -> dict:
    """Call the DeHashed v2 API. Returns the parsed JSON response dict."""
    api_key = _get_api_key()

    headers = {
        "Accept":           "application/json",
        "Content-Type":     "application/json",
        "Dehashed-Api-Key": api_key,
    }
    payload = {
        "query": _build_query(raw_query, field),
        "size":  min(size, MAX_SIZE),
        "page":  page,
    }

    try:
        resp = requests.post(DEHASHED_URL, headers=headers, json=payload, timeout=20)
    except requests.ConnectionError as e:
        print(f"ERROR: Connection failed — {e}", file=sys.stderr)
        sys.exit(1)
    except requests.Timeout:
        print("ERROR: Request timed out", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 400:
        msg = resp.json().get("error", resp.text[:200]) if resp.text else "check your query syntax"
        print(f"ERROR: Bad request — {msg}", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 401:
        msg = resp.json().get("error", "") if resp.text else ""
        hint = f" — {msg}" if msg else " — check DEHASHED_API_KEY and ensure you have an active subscription"
        print(f"ERROR: Authentication failed{hint}", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 402:
        print("ERROR: Insufficient credits — recharge at dehashed.com", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 429:
        print("ERROR: Rate limited — wait a moment and retry", file=sys.stderr)
        sys.exit(1)
    if not resp.ok:
        print(f"ERROR: HTTP {resp.status_code} — {resp.text[:200]}", file=sys.stderr)
        sys.exit(1)

    try:
        return resp.json()
    except ValueError:
        print(f"ERROR: Invalid JSON response — {resp.text[:200]}", file=sys.stderr)
        sys.exit(1)


# ── Output formatting ─────────────────────────────────────────────────────────

def _val(entry: dict, key: str) -> str:
    """Return a field value or empty string."""
    v = entry.get(key)
    return str(v).strip() if v else ""


def format_results(data: dict, raw_query: str, field: str) -> str:
    """Format the DeHashed response as human-readable text."""
    entries = data.get("entries") or []
    total   = data.get("total", 0)
    balance = data.get("balance", "?")

    lines = []
    width = 70
    lines.append("═" * width)
    lines.append(f"  DeHashed Search: {field}:{raw_query}")
    lines.append(f"  Results: {len(entries)} shown of {total:,} total  |  Credits remaining: {balance:,}" if isinstance(balance, int) else f"  Results: {len(entries)} shown of {total:,} total")
    lines.append("═" * width)

    if not entries:
        lines.append("\n  No results found.")
        lines.append("═" * width)
        return "\n".join(lines)

    for i, entry in enumerate(entries, 1):
        lines.append(f"\n  [{i}]  Database: {_val(entry, 'database_name') or 'unknown'}")
        for key, label in DISPLAY_FIELDS:
            if key == "database_name":
                continue
            v = _val(entry, key)
            if v:
                lines.append(f"       {label:<16} {v}")

    lines.append("\n" + "═" * width)
    return "\n".join(lines)


# ── File output ───────────────────────────────────────────────────────────────

def _auto_output_path(raw_query: str, field: str) -> str:
    """Generate a timestamped path under .tmp/."""
    base_dir = os.path.join(os.path.dirname(__file__), "..", ".tmp")
    os.makedirs(base_dir, exist_ok=True)
    slug = re.sub(r"[^\w\-]", "_", raw_query)[:40]
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.abspath(os.path.join(base_dir, f"dehashed_{field}_{slug}_{ts}.txt"))


def _save_output(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content + "\n")
    print(f"[auto-saved → {path}]")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] in ("/help", "--help", "-h", "help"):
        print(HELP_TEXT)
        return

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--query",  required=True)
    parser.add_argument("--field",  default=None, choices=list(FIELDS.keys()))
    parser.add_argument("--size",   type=int, default=DEFAULT_SIZE)
    parser.add_argument("--page",   type=int, default=1)
    parser.add_argument("--output", default=None)
    parser.add_argument("--json",   action="store_true", dest="json_out")
    args = parser.parse_args()

    # Resolve field
    if args.field:
        field = FIELDS[args.field]
    else:
        field = _autodetect_field(args.query)
        print(f"[auto-detected field: {field}]", file=sys.stderr)

    data = query_dehashed(args.query, field, size=args.size, page=args.page)

    if args.json_out:
        output = json.dumps(data, indent=2)
    else:
        output = format_results(data, args.query, field)

    print(output)

    out_path = args.output or _auto_output_path(args.query, field)
    _save_output(out_path, output)


if __name__ == "__main__":
    main()
