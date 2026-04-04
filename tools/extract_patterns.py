"""
extract_patterns.py — Regex and structured-data extraction utilities.
Shared by scrape_single_site.py and search_and_scrape.py.
"""

import re
import json
from typing import List, Dict, Any


# ── Phone ──────────────────────────────────────────────────────────────────
_PHONE_RE = re.compile(
    r"""
    (?<!\d)
    (?:\+?1[\s.\-]?)?           # optional country code
    (?:\(?(\d{3})\)?[\s.\-]?)   # area code
    (\d{3})[\s.\-](\d{4})       # local number
    (?:[\s]*(?:ext|x|ext\.)[\s]*(\d{1,6}))?  # optional extension
    (?!\d)
    """,
    re.VERBOSE,
)

_INTL_PHONE_RE = re.compile(
    r"\+(?!1\b)\d{1,3}[\s.\-]\d{1,5}(?:[\s.\-]\d{1,5}){1,4}"
)


def extract_phones(text: str) -> List[str]:
    results = []
    seen = set()
    for m in _PHONE_RE.finditer(text):
        area, prefix, line, ext = m.group(1), m.group(2), m.group(3), m.group(4)
        if not area:
            continue
        num = f"({area}) {prefix}-{line}"
        if ext:
            num += f" ext. {ext}"
        if num not in seen:
            seen.add(num)
            results.append(num)
    for m in _INTL_PHONE_RE.finditer(text):
        raw = m.group(0).strip()
        if raw not in seen:
            seen.add(raw)
            results.append(raw)
    return results


# ── Email ──────────────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)


def extract_emails(text: str) -> List[str]:
    raw = _EMAIL_RE.findall(text)
    seen = set()
    out = []
    for e in raw:
        el = e.lower()
        # skip image/asset emails and common false positives
        if any(el.endswith(x) for x in (".png", ".jpg", ".gif", ".css", ".js")):
            continue
        if el not in seen:
            seen.add(el)
            out.append(e)
    return out


# ── Address ────────────────────────────────────────────────────────────────
_STREET_TYPES = (
    r"Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Way|"
    r"Lane|Ln|Court|Ct|Place|Pl|Parkway|Pkwy|Highway|Hwy|Suite|Ste|Floor|Fl|Unit|Apt"
)
_ADDR_RE = re.compile(
    r"\b\d{1,6}"
    r"\s+[A-Za-z0-9][A-Za-z0-9\s\.]{2,40}"
    r"(?:" + _STREET_TYPES + r")\b\.?"
    r"(?:[,\s]+(?:Suite|Ste|Fl|Floor|Unit|Apt|#)\s*[\w\-]+)?"
    r"[,\s]+[A-Za-z][A-Za-z\s]{1,29}"
    r",?\s*[A-Z]{2}"
    r"\s+\d{5}(?:-\d{4})?",
    re.IGNORECASE,
)


def extract_addresses(text: str) -> List[str]:
    results = []
    seen = set()
    for m in _ADDR_RE.finditer(text):
        addr = re.sub(r"\s+", " ", m.group(0)).strip().strip(",")
        if addr not in seen:
            seen.add(addr)
            results.append(addr)
    return results


# ── JSON-LD / schema.org ───────────────────────────────────────────────────
_RELEVANT_TYPES = {
    "Organization", "LocalBusiness", "Store", "Restaurant", "Hotel",
    "MedicalBusiness", "GovernmentOrganization", "NGO", "Corporation",
    "Person", "ContactPoint",
}


def _flatten_jsonld(node: Any, out: Dict) -> None:
    """Recursively pull contact fields from a JSON-LD node."""
    if isinstance(node, list):
        for item in node:
            _flatten_jsonld(item, out)
        return
    if not isinstance(node, dict):
        return

    t = node.get("@type", "")
    if isinstance(t, list):
        t = t[0] if t else ""

    if t in _RELEVANT_TYPES or not t:
        for field, key in [
            ("telephone", "phones"),
            ("faxNumber", "faxes"),
            ("email", "emails"),
        ]:
            val = node.get(field)
            if val:
                if isinstance(val, list):
                    out.setdefault(key, []).extend(val)
                else:
                    out.setdefault(key, []).append(val)

        addr = node.get("address")
        if addr:
            if isinstance(addr, dict):
                parts = [
                    addr.get("streetAddress", ""),
                    addr.get("addressLocality", ""),
                    addr.get("addressRegion", ""),
                    addr.get("postalCode", ""),
                    addr.get("addressCountry", ""),
                ]
                full = ", ".join(p for p in parts if p)
                if full:
                    out.setdefault("addresses", []).append(full)
            elif isinstance(addr, str):
                out.setdefault("addresses", []).append(addr)

        for field in ("name", "legalName"):
            val = node.get(field)
            if val and isinstance(val, str):
                out.setdefault("names", []).append(val)

        url = node.get("url")
        if url and isinstance(url, str):
            out.setdefault("urls", []).append(url)

    # recurse into child nodes
    for v in node.values():
        if isinstance(v, (dict, list)):
            _flatten_jsonld(v, out)


def extract_from_jsonld(scripts: List[str]) -> Dict:
    out: Dict = {}
    for raw in scripts:
        try:
            data = json.loads(raw)
            _flatten_jsonld(data, out)
        except (json.JSONDecodeError, ValueError):
            pass
    # deduplicate
    for k in list(out.keys()):
        seen = set()
        deduped = []
        for v in out[k]:
            if v not in seen:
                seen.add(v)
                deduped.append(v)
        out[k] = deduped
    return out
