#!/usr/bin/env python3
"""
search_social_media.py — Search social media platforms for profiles matching a subject.

Usage:
  python search_social_media.py --query "Jane Smith"
  python search_social_media.py --query "Jane Smith Boise ID" --platforms instagram tiktok facebook
  python search_social_media.py --query "janesmith92" --mode username
  python search_social_media.py --query "Jane Smith" --output .tmp/jane_smith_social.txt
"""

import argparse
import os
import sys
import time
from datetime import datetime
from urllib.parse import urlparse

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
    print("ERROR: Missing dependencies. Run: pip install requests beautifulsoup4")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(__file__))
from extract_patterns import extract_emails, extract_phones

try:
    from generate_social_html import generate_html, save_html as _save_html
    _HTML_AVAILABLE = True
except ImportError:
    _HTML_AVAILABLE = False

# ── RapidAPI TikTok integration ────────────────────────────────────────────

# Supported RapidAPI TikTok hosts and their endpoint schemas
_TIKTOK_API_SCHEMAS = {
    # Confirmed working: /api/user/info?uniqueId=username
    # Response: {"userInfo": {"user": {...}, "stats": {...}, "statsV2": {...}}}
    "tiktok-api23.p.rapidapi.com": {
        "userinfo_url": "https://tiktok-api23.p.rapidapi.com/api/user/info",
        "userinfo_params": lambda u: {"uniqueId": u},
        "userinfo_path": lambda r: {
            **(r.get("userInfo", {}).get("user", {})),
            "_stats": r.get("userInfo", {}).get("statsV2",
                      r.get("userInfo", {}).get("stats", {})),
        },
        "parse_userinfo": lambda u: {
            "username": u.get("uniqueId", ""),
            "nickname": u.get("nickname", ""),
            "bio":      u.get("signature", ""),
            "followers": u.get("_stats", {}).get("followerCount", ""),
            "following": u.get("_stats", {}).get("followingCount", ""),
            "videos":    u.get("_stats", {}).get("videoCount", ""),
            "verified":  u.get("verified", False),
            "likes":     u.get("_stats", {}).get("heartCount", ""),
            "avatar":    u.get("avatarLarger") or u.get("avatarMedium") or u.get("avatarThumb", ""),
        },
    },
    "tiktok-scraper7.p.rapidapi.com": {
        "userinfo_url": "https://tiktok-scraper7.p.rapidapi.com/user/info",
        "userinfo_params": lambda u: {"username": u},
        "userinfo_path": lambda r: {
            **(r.get("data", {}).get("user",
               r.get("userInfo", {}).get("user", {}))),
            "_stats": r.get("data", {}).get("stats",
                      r.get("userInfo", {}).get("stats", {})),
        },
        "parse_userinfo": lambda u: {
            "username": u.get("uniqueId", ""),
            "nickname": u.get("nickname", ""),
            "bio":      u.get("signature", ""),
            "followers": u.get("_stats", {}).get("followerCount", ""),
            "following": u.get("_stats", {}).get("followingCount", ""),
            "videos":    u.get("_stats", {}).get("videoCount", ""),
            "verified":  u.get("verified", False),
            "likes":     u.get("_stats", {}).get("heartCount", ""),
            "avatar":    u.get("avatarLarger") or u.get("avatarMedium") or u.get("avatarThumb", ""),
        },
    },
    "tiktok82.p.rapidapi.com": {
        "userinfo_url": "https://tiktok82.p.rapidapi.com/api/user/info/",
        "userinfo_params": lambda u: {"uniqueId": u},
        "userinfo_path": lambda r: {
            **(r.get("data", {}).get("user", {})),
            "_stats": r.get("data", {}).get("stats", {}),
        },
        "parse_userinfo": lambda u: {
            "username": u.get("uniqueId", ""),
            "nickname": u.get("nickname", ""),
            "bio":      u.get("signature", ""),
            "followers": u.get("_stats", {}).get("followerCount", ""),
            "following": u.get("_stats", {}).get("followingCount", ""),
            "videos":    u.get("_stats", {}).get("videoCount", ""),
            "verified":  u.get("verified", False),
            "likes":     "",
            "avatar":    u.get("avatarLarger") or u.get("avatarMedium") or u.get("avatarThumb", ""),
        },
    },
}

RAPIDAPI_TIMEOUT = 15


def _get_tiktok_schema():
    """Return the API schema dict for the configured host, or None if not set."""
    key = os.environ.get("RAPIDAPI_KEY", "")
    host = os.environ.get("RAPIDAPI_TIKTOK_HOST", "tiktok-api23.p.rapidapi.com").strip()
    if not key:
        return None, None, None
    schema = _TIKTOK_API_SCHEMAS.get(host)
    if not schema:
        # Unknown host — try the default schema and hope endpoint shapes match
        schema = _TIKTOK_API_SCHEMAS["tiktok-api23.p.rapidapi.com"]
    return key, host, schema


def _rapidapi_headers(key: str, host: str) -> dict:
    return {
        "X-RapidAPI-Key": key,
        "X-RapidAPI-Host": host,
        "Accept": "application/json",
    }


def _fmt_count(n) -> str:
    """Format follower/like counts for display."""
    if n == "" or n is None:
        return ""
    try:
        n = int(n)
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}K"
        return str(n)
    except (ValueError, TypeError):
        return str(n)


def _lookup_tiktok_username(username: str, key: str, host: str, schema: dict) -> dict:
    """Look up a single TikTok username via RapidAPI. Returns parsed dict or {}."""
    try:
        headers = _rapidapi_headers(key, host)
        url = schema["userinfo_url"]
        params = schema["userinfo_params"](username)
        resp = requests.get(url, headers=headers, params=params, timeout=RAPIDAPI_TIMEOUT)
        if resp.status_code not in (200,):
            return {}
        data = resp.json()
        user = schema["userinfo_path"](data)
        if not user or not user.get("uniqueId"):
            return {}
        return schema["parse_userinfo"](user)
    except Exception:
        return {}


def _ddg_find_tiktok_usernames(query: str) -> list:
    """Use DDG to find likely TikTok profile URLs, return extracted usernames."""
    hits = ddg_search(f'"{query}" site:tiktok.com', max_results=8)
    usernames = []
    seen = set()
    for h in hits:
        url = h.get("url", "")
        if "tiktok.com/@" not in url:
            continue
        # Extract @username from URL
        try:
            path = urlparse(url).path  # /@username or /@username/video/...
            parts = path.strip("/").split("/")
            if parts and parts[0].startswith("@"):
                uname = parts[0][1:]  # strip @
                if uname and uname not in seen:
                    seen.add(uname)
                    usernames.append(uname)
        except Exception:
            continue
    return usernames


def search_tiktok_api(query: str, mode: str = "name") -> list:
    """
    Hybrid TikTok search:
      - For username mode: direct API lookup
      - For name mode: DDG discovers profile URLs → API enriches each with
        accurate follower counts, bio, verified status
    Falls back to empty list (caller uses DDG-only) if API unavailable.
    """
    key, configured_host, _ = _get_tiktok_schema()
    if not key:
        return []

    # Find working host
    hosts_to_try = [configured_host] + [h for h in _TIKTOK_API_SCHEMAS if h != configured_host]
    working_host = None
    working_schema = None
    for host in hosts_to_try:
        schema = _TIKTOK_API_SCHEMAS[host]
        # Quick probe
        try:
            headers = _rapidapi_headers(key, host)
            r = requests.get(schema["userinfo_url"], headers=headers,
                             params=schema["userinfo_params"]("tiktok"), timeout=10)
            if r.status_code == 200:
                working_host = host
                working_schema = schema
                break
            else:
                print(f"    [RapidAPI {host} → {r.status_code}]", file=sys.stderr)
        except Exception as e:
            print(f"    [RapidAPI {host} error: {e}]", file=sys.stderr)

    if not working_host:
        print(f"    [RapidAPI TikTok: no working host — falling back to DDG]", file=sys.stderr)
        return []

    print(f"    [RapidAPI TikTok ✓ via {working_host}]", file=sys.stderr)

    if mode == "username":
        result = _lookup_tiktok_username(query, key, working_host, working_schema)
        if result and result.get("username"):
            result["url"] = f"https://www.tiktok.com/@{result['username']}"
            return [result]
        return []

    # Name mode: DDG discovers → API enriches
    usernames = _ddg_find_tiktok_usernames(query)
    if not usernames:
        return []

    enriched = []
    for uname in usernames[:5]:
        profile = _lookup_tiktok_username(uname, key, working_host, working_schema)
        if profile and profile.get("username"):
            profile["url"] = f"https://www.tiktok.com/@{profile['username']}"
            enriched.append(profile)
        time.sleep(0.3)

    return enriched


def _name_matches_tiktok(query: str, profile: dict) -> bool:
    """Check if a TikTok API result likely belongs to the queried person."""
    _IGNORE = {"in", "at", "ca", "id", "va", "tx", "ny", "fl", "or", "wa",
               "sc", "the", "and", "of"}
    words = [w.lower() for w in query.split() if len(w) > 2 and w.lower() not in _IGNORE]
    if not words:
        return True
    combined = (
        (profile.get("username") or "") + " " +
        (profile.get("nickname") or "") + " " +
        (profile.get("bio") or "")
    ).lower()
    return any(w in combined for w in words)

# ── Platform definitions ───────────────────────────────────────────────────
PLATFORMS = {
    "instagram": {
        "label": "Instagram",
        "domain": "instagram.com",
        "profile_prefix": "instagram.com/",
        "query_tmpl": '"{name}" site:instagram.com',
        "username_tmpl": 'site:instagram.com "{username}"',
    },
    "tiktok": {
        "label": "TikTok",
        "domain": "tiktok.com",
        "profile_prefix": "tiktok.com/@",
        "query_tmpl": '"{name}" site:tiktok.com',
        "username_tmpl": 'site:tiktok.com "@{username}"',
    },
    "facebook": {
        "label": "Facebook",
        "domain": "facebook.com",
        "profile_prefix": "facebook.com/",
        "query_tmpl": '"{name}" site:facebook.com',
        "username_tmpl": 'site:facebook.com "{username}"',
    },
    "twitter": {
        "label": "Twitter/X",
        "domain": "x.com",
        "alt_domain": "twitter.com",
        "profile_prefix": "x.com/",
        "query_tmpl": '"{name}" site:x.com OR site:twitter.com',
        "username_tmpl": 'site:x.com "@{username}" OR site:twitter.com "@{username}"',
    },
    "linkedin": {
        "label": "LinkedIn",
        "domain": "linkedin.com",
        "profile_prefix": "linkedin.com/in/",
        "query_tmpl": '"{name}" site:linkedin.com/in',
        "username_tmpl": 'site:linkedin.com/in "{username}"',
    },
    "github": {
        "label": "GitHub",
        "domain": "github.com",
        "profile_prefix": "github.com/",
        "query_tmpl": '"{name}" site:github.com',
        "username_tmpl": 'site:github.com "{username}"',
    },
    "youtube": {
        "label": "YouTube",
        "domain": "youtube.com",
        "profile_prefix": "youtube.com/@",
        "query_tmpl": '"{name}" site:youtube.com',
        "username_tmpl": 'site:youtube.com "{username}"',
    },
    "reddit": {
        "label": "Reddit",
        "domain": "reddit.com",
        "profile_prefix": "reddit.com/user/",
        "query_tmpl": '"{name}" site:reddit.com/user',
        "username_tmpl": 'site:reddit.com/user "{username}"',
    },
    "pinterest": {
        "label": "Pinterest",
        "domain": "pinterest.com",
        "profile_prefix": "pinterest.com/",
        "query_tmpl": '"{name}" site:pinterest.com',
        "username_tmpl": 'site:pinterest.com "{username}"',
    },
    "snapchat": {
        "label": "Snapchat",
        "domain": "snapchat.com",
        "profile_prefix": "snapchat.com/add/",
        "query_tmpl": '"{name}" site:snapchat.com/add',
        "username_tmpl": 'site:snapchat.com/add "{username}"',
    },
}

DEFAULT_PLATFORMS = ["instagram", "tiktok", "facebook", "twitter", "linkedin",
                     "github", "youtube", "reddit"]

# Paths that indicate a page result is a profile, not a post/article
_PROFILE_INDICATORS = [
    "/in/",       # LinkedIn
    "/@",         # YouTube, TikTok
    "/user/",     # Reddit
    "/add/",      # Snapchat
]

# Paths to skip — not profile pages
_SKIP_PATH_FRAGMENTS = [
    "/posts/", "/videos/", "/photos/", "/reels/", "/stories/",
    "/watch?", "/hashtag/", "/explore/", "/search/", "/help/",
    "/legal/", "/privacy", "/about/", "/ads/", "/business/",
    "sharer", "share?", "intent/tweet",
    # GitHub repo content — not profiles
    "/blob/", "/tree/", "/commit/", "/issues/", "/pulls/",
    "/releases/", "/wiki/", "/actions/", "/discussions/",
]


def _is_likely_profile(url: str, platform_key: str) -> bool:
    """Heuristic: is this URL likely a user profile page?"""
    parsed = urlparse(url)
    path = parsed.path.lower()

    for frag in _SKIP_PATH_FRAGMENTS:
        if frag in path or frag in url.lower():
            return False

    # LinkedIn: must be /in/ path
    if platform_key == "linkedin" and "/in/" not in path:
        return False

    # Reddit: must be /user/ path
    if platform_key == "reddit" and "/user/" not in path:
        return False

    # Snapchat: must be /add/ path
    if platform_key == "snapchat" and "/add/" not in path:
        return False

    # TikTok: must be /@username
    if platform_key == "tiktok" and "/@" not in path and "/@" not in url:
        return False

    # YouTube: must be /@ or /channel/ or /c/
    if platform_key == "youtube":
        if not any(x in path for x in ["/@", "/channel/", "/c/"]):
            return False

    return True


def ddg_search(query: str, max_results: int = 5) -> list:
    """Run a DuckDuckGo search and return result dicts."""
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
        pass
    time.sleep(0.4)  # polite rate limiting between queries
    return results


def extract_username_from_url(url: str, platform_key: str) -> str:
    """Pull the username/handle from a social profile URL."""
    path = urlparse(url).path.strip("/")
    platform = PLATFORMS[platform_key]

    if platform_key == "linkedin":
        if "/in/" in path:
            return path.split("/in/")[-1].split("/")[0]
    elif platform_key == "reddit":
        if "user/" in path:
            return path.split("user/")[-1].split("/")[0]
    elif platform_key == "snapchat":
        if "add/" in path:
            return path.split("add/")[-1].split("/")[0]
    elif platform_key in ("tiktok", "youtube"):
        if "@" in path:
            return "@" + path.split("@")[-1].split("/")[0]
        parts = path.split("/")
        return parts[-1] if parts else ""
    else:
        parts = path.split("/")
        return parts[0] if parts else ""

    return path.split("/")[0] if path else ""


def search_platform(name: str, platform_key: str, mode: str = "name",
                    extra_context: str = "") -> dict:
    """Search one platform for the given name/username. Returns result dict."""
    platform = PLATFORMS[platform_key]
    label = platform["label"]

    # ── TikTok: use RapidAPI if key is set, else fall back to DDG ─────────
    if platform_key == "tiktok":
        key, host, _ = _get_tiktok_schema()
        if key:
            print(f"    [RapidAPI → {host}]", file=sys.stderr)
            api_results = search_tiktok_api(name, mode=mode)
            if api_results:  # only use API results if we actually got something
                matched = [r for r in api_results if _name_matches_tiktok(name, r)]
                profiles = []
                for r in matched[:5]:
                    bio_parts = []
                    if r.get("nickname") and r["nickname"] != r.get("username"):
                        bio_parts.append(r["nickname"])
                    if r.get("bio"):
                        bio_parts.append(r["bio"])
                    stats = []
                    if r.get("followers") != "":
                        stats.append(f"{_fmt_count(r['followers'])} followers")
                    if r.get("following") != "":
                        stats.append(f"{_fmt_count(r['following'])} following")
                    if r.get("videos") != "":
                        stats.append(f"{_fmt_count(r['videos'])} videos")
                    if r.get("verified"):
                        stats.append("✓ verified")
                    if stats:
                        bio_parts.append(" | ".join(stats))
                    profiles.append({
                        "url": r["url"],
                        "username": f"@{r['username']}",
                        "title": r.get("nickname", ""),
                        "bio": "  ".join(bio_parts),
                        "avatar": r.get("avatar", ""),
                        "followers": r.get("followers", ""),
                        "following": r.get("following", ""),
                        "videos": r.get("videos", ""),
                        "likes": r.get("likes", ""),
                        "verified": r.get("verified", False),
                        "platform": label,
                    })
                return {
                    "platform": label,
                    "platform_key": platform_key,
                    "query": name,
                    "profiles": profiles,
                    "source": f"RapidAPI ({host})",
                }
            # API failed or returned nothing — fall through to DDG
            print(f"    [TikTok: falling back to DDG search]", file=sys.stderr)

    # ── All other platforms (and TikTok fallback): DDG search ─────────────
    if mode == "username":
        query = platform["username_tmpl"].format(username=name)
    else:
        query = platform["query_tmpl"].format(name=name)
        if extra_context:
            query += f" {extra_context}"

    hits = ddg_search(query, max_results=6)
    domain = platform["domain"]
    alt_domain = platform.get("alt_domain", "")

    profiles = []
    seen_urls = set()

    for hit in hits:
        url = hit.get("url", "")
        if not url:
            continue
        host = urlparse(url).netloc.lower()
        if domain not in host and (not alt_domain or alt_domain not in host):
            continue
        if not _is_likely_profile(url, platform_key):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        username = extract_username_from_url(url, platform_key)
        snippet = hit.get("snippet", "")
        title = hit.get("title", "")
        bio = snippet[:200] if snippet else ""

        profiles.append({
            "url": url,
            "username": username,
            "title": title,
            "bio": bio,
            "platform": label,
        })

    return {
        "platform": label,
        "platform_key": platform_key,
        "query": query,
        "profiles": profiles,
        "source": "DuckDuckGo",
    }


def search_all_platforms(name: str, platforms: list, mode: str = "name",
                         extra_context: str = "") -> list:
    """Search all specified platforms. Returns list of platform result dicts."""
    results = []
    for p_key in platforms:
        if p_key not in PLATFORMS:
            print(f"  [unknown platform: {p_key}]", file=sys.stderr)
            continue
        label = PLATFORMS[p_key]["label"]
        print(f"  Searching {label}...", file=sys.stderr)
        r = search_platform(name, p_key, mode=mode, extra_context=extra_context)
        results.append(r)
    return results


_POST_PATH_FRAGMENTS = ["/p/", "/reel/", "/tv/", "/watch", "/status/",
                        "/posts/", "/videos/", "/photos/"]


def _is_post(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(f in path for f in _POST_PATH_FRAGMENTS)


def _name_matches(query: str, profile: dict) -> bool:
    """
    Loose check: does this profile likely belong to the queried person?
    Checks username and bio/title snippet for any word from the query name.
    Filters out obvious false positives (e.g. unrelated Twitter accounts).
    """
    # Extract words from query (ignore short words and location tokens)
    _IGNORE = {"in", "at", "ca", "id", "va", "tx", "ny", "fl", "or", "wa",
               "the", "and", "of"}
    words = [w.lower() for w in query.split() if len(w) > 2 and w.lower() not in _IGNORE]
    if not words:
        return True  # can't filter without name words

    username = (profile.get("username") or "").lower()
    bio = (profile.get("bio") or "").lower()
    title = (profile.get("title") or "").lower()
    combined = f"{username} {bio} {title}"

    # At least one name word must appear somewhere in the profile data
    return any(w in combined for w in words)


def format_results(results: list, query: str) -> str:
    """Format platform results — profiles first, post snippets as context."""
    lines = []
    found_any = False

    for r in results:
        label = r["platform"]
        all_hits = r["profiles"]

        profiles = [p for p in all_hits if not _is_post(p["url"]) and _name_matches(query, p)]
        posts = [p for p in all_hits if _is_post(p["url"]) and _name_matches(query, p)]

        if not profiles and not posts:
            lines.append(f"  {label:12}  not found")
            continue

        found_any = True

        if profiles:
            # Show confirmed profile(s)
            for p in profiles:
                username = p["username"]
                if username and not username.startswith("@"):
                    username = f"@{username}"
                lines.append(f"  {label:12}  {username or '(profile)'}  →  {p['url']}")
                if p["bio"]:
                    bio_short = p["bio"][:140].replace("\n", " ").strip()
                    lines.append(f"{'':16}Bio: {bio_short}")

        # Show post snippets as context (no URL spam — just the text)
        if posts:
            if not profiles:
                lines.append(f"  {label:12}  (no profile page — activity found)")
            lines.append(f"{'':16}Posts/activity:")
            for p in posts[:4]:
                if p["bio"]:
                    snippet = p["bio"][:130].replace("\n", " ").strip()
                    lines.append(f"{'':18}• {snippet}")

    if not found_any:
        lines.append("  No social profiles found across searched platforms.")

    return "\n".join(lines)


def save_to_file(content: str, output_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(content + "\n")


def main():
    parser = argparse.ArgumentParser(description="Search social media platforms for a person or username")
    parser.add_argument("--query", required=True,
                        help="Person name (e.g. 'Jane Smith') or username (e.g. 'jsmith92')")
    parser.add_argument("--mode", choices=["name", "username"], default="name",
                        help="Search mode: 'name' (default) or 'username'")
    parser.add_argument("--context", default="",
                        help="Optional extra context to narrow results (e.g. 'Boise ID')")
    parser.add_argument("--platforms", nargs="+",
                        choices=list(PLATFORMS.keys()) + ["all"],
                        default=DEFAULT_PLATFORMS,
                        help="Platforms to search (default: instagram tiktok facebook twitter linkedin github youtube reddit)")
    parser.add_argument("--output", default="",
                        help="Append results to this file path")
    args = parser.parse_args()

    platforms = list(PLATFORMS.keys()) if "all" in args.platforms else args.platforms
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"\nSocial media search: {args.query!r}  [{ts}]")
    print(f"Platforms: {', '.join(PLATFORMS[p]['label'] for p in platforms)}\n")

    results = search_all_platforms(
        name=args.query,
        platforms=platforms,
        mode=args.mode,
        extra_context=args.context,
    )

    header = f"=== SOCIAL MEDIA SEARCH: {args.query!r} | {ts} ===\n"
    body = format_results(results, args.query)
    output = header + body + "\n"

    print("\n" + output)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    tmp_dir = os.path.join(script_dir, "..", ".tmp")
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in args.query)[:40]
    ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.output:
        save_to_file(output, args.output)
        print(f"[saved → {args.output}]")
    else:
        auto_path = os.path.join(tmp_dir, f"social_{safe}_{ts_file}.txt")
        save_to_file(output, auto_path)
        print(f"[auto-saved → {auto_path}]")

    # ── HTML report ────────────────────────────────────────────────────────
    if _HTML_AVAILABLE:
        # Filter results to only matched profiles (same logic as format_results)
        html_results = []
        for r in results:
            matched = [p for p in r["profiles"] if _name_matches(args.query, p)]
            html_results.append({**r, "profiles": matched})

        html_content = generate_html(html_results, args.query, fetch_images=True)
        html_path = os.path.join(tmp_dir, f"social_{safe}_{ts_file}.html")
        _save_html(html_content, html_path)
        print(f"[HTML report → {html_path}]")
        # Auto-open on macOS
        try:
            import subprocess
            subprocess.Popen(["open", html_path])
        except Exception:
            pass


if __name__ == "__main__":
    main()
