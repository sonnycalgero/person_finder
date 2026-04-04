#!/usr/bin/env python3
"""
generate_social_html.py — Generate an HTML report from social media search results.
Called by search_social_media.py automatically, or standalone.

Usage:
  Typically called internally — results passed as a dict.
  Standalone: python generate_social_html.py  (for testing)
"""

import os
import sys
from datetime import datetime

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    pass

# ── Platform styling ───────────────────────────────────────────────────────
PLATFORM_STYLES = {
    "Instagram": {
        "color": "#E1306C",
        "bg": "#fdf0f5",
        "icon": "📸",
        "gradient": "linear-gradient(45deg, #f09433, #e6683c, #dc2743, #cc2366, #bc1888)",
    },
    "TikTok": {
        "color": "#000000",
        "bg": "#f0f0f0",
        "icon": "🎵",
        "gradient": "linear-gradient(135deg, #010101 0%, #69C9D0 50%, #EE1D52 100%)",
    },
    "Facebook": {
        "color": "#1877F2",
        "bg": "#f0f4ff",
        "icon": "👤",
        "gradient": "linear-gradient(135deg, #1877F2, #42a5f5)",
    },
    "Twitter/X": {
        "color": "#000000",
        "bg": "#f5f5f5",
        "icon": "✕",
        "gradient": "linear-gradient(135deg, #14171A, #657786)",
    },
    "LinkedIn": {
        "color": "#0077B5",
        "bg": "#f0f6fb",
        "icon": "💼",
        "gradient": "linear-gradient(135deg, #0077B5, #00a0dc)",
    },
    "GitHub": {
        "color": "#333333",
        "bg": "#f6f8fa",
        "icon": "💻",
        "gradient": "linear-gradient(135deg, #24292e, #586069)",
    },
    "YouTube": {
        "color": "#FF0000",
        "bg": "#fff5f5",
        "icon": "▶",
        "gradient": "linear-gradient(135deg, #FF0000, #ff6b6b)",
    },
    "Reddit": {
        "color": "#FF4500",
        "bg": "#fff5f0",
        "icon": "🤖",
        "gradient": "linear-gradient(135deg, #FF4500, #ff7043)",
    },
    "Pinterest": {
        "color": "#E60023",
        "bg": "#fff0f1",
        "icon": "📌",
        "gradient": "linear-gradient(135deg, #E60023, #ff4d67)",
    },
    "Snapchat": {
        "color": "#FFFC00",
        "bg": "#fffef0",
        "icon": "👻",
        "gradient": "linear-gradient(135deg, #FFFC00, #FFD700)",
    },
}

_DEFAULT_STYLE = {
    "color": "#555555",
    "bg": "#f9f9f9",
    "icon": "🔗",
    "gradient": "linear-gradient(135deg, #555, #888)",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def fetch_og_image(url: str) -> str:
    """Try to get og:image from a profile URL. Returns image URL or ''."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]
        # Twitter card fallback
        tc = soup.find("meta", attrs={"name": "twitter:image"})
        if tc and tc.get("content"):
            return tc["content"]
    except Exception:
        pass
    return ""


def _platform_header_card(platform: str, count: int) -> str:
    style = PLATFORM_STYLES.get(platform, _DEFAULT_STYLE)
    return f"""
    <div class="platform-section">
        <div class="platform-header" style="background:{style['gradient']}">
            <span class="platform-icon">{style['icon']}</span>
            <span class="platform-name">{platform}</span>
            <span class="profile-count">{count} profile{'s' if count != 1 else ''} found</span>
        </div>
    """


def _avatar_html(avatar_url: str, profile_url: str, username: str, platform: str) -> str:
    style = PLATFORM_STYLES.get(platform, _DEFAULT_STYLE)
    initials = (username.lstrip("@")[:2] or "?").upper()

    if avatar_url:
        return f"""
        <div class="avatar-wrap">
            <img src="{avatar_url}"
                 alt="{username}"
                 class="avatar"
                 onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
            <div class="avatar-fallback" style="background:{style['gradient']};display:none">
                {initials}
            </div>
        </div>"""
    else:
        return f"""
        <div class="avatar-wrap">
            <div class="avatar-fallback" style="background:{style['gradient']}">
                {initials}
            </div>
        </div>"""


def _stat_badge(label: str, value: str) -> str:
    if not value and value != 0:
        return ""
    return f'<span class="stat-badge"><strong>{value}</strong> {label}</span>'


def _profile_card(profile: dict, platform: str, fetch_images: bool = True) -> str:
    style = PLATFORM_STYLES.get(platform, _DEFAULT_STYLE)
    url = profile.get("url", "#")
    username = profile.get("username", "")
    if username and not username.startswith("@"):
        username = f"@{username}"
    nickname = profile.get("title", "") or profile.get("nickname", "")
    bio = profile.get("bio", "").replace("<", "&lt;").replace(">", "&gt;")
    avatar = profile.get("avatar", "")

    # Try og:image for non-TikTok platforms if no avatar yet
    if not avatar and fetch_images and url and url != "#":
        avatar = fetch_og_image(url)

    avatar_html = _avatar_html(avatar, url, username, platform)

    # Stats
    stats_html = ""
    for label, key in [("followers", "followers"), ("following", "following"),
                       ("videos", "videos"), ("likes", "likes")]:
        val = profile.get(key, "")
        if val:
            stats_html += _stat_badge(label, str(val))

    verified_badge = ""
    if profile.get("verified"):
        verified_badge = '<span class="verified-badge">✓ Verified</span>'

    # Trim bio for display
    bio_short = bio[:200] + ("…" if len(bio) > 200 else "")

    return f"""
    <div class="profile-card" style="border-top: 3px solid {style['color']}">
        <a href="{url}" target="_blank" rel="noopener" class="card-link">
            {avatar_html}
            <div class="card-body">
                <div class="card-username">{username} {verified_badge}</div>
                {"<div class='card-nickname'>" + nickname + "</div>" if nickname and nickname != username.lstrip("@") else ""}
                {"<div class='card-bio'>" + bio_short + "</div>" if bio_short else ""}
                {"<div class='card-stats'>" + stats_html + "</div>" if stats_html else ""}
                <div class="card-url">{url}</div>
            </div>
        </a>
    </div>"""


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Social Media Search: {query}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f2f4f7;
    color: #1a1a1a;
    padding: 24px;
  }}
  .page-header {{
    background: linear-gradient(135deg, #1a1a2e, #16213e, #0f3460);
    color: white;
    border-radius: 16px;
    padding: 32px 40px;
    margin-bottom: 32px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.2);
  }}
  .page-header h1 {{ font-size: 28px; margin-bottom: 8px; }}
  .page-header .meta {{ opacity: 0.7; font-size: 14px; }}
  .summary-bar {{
    display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 32px;
  }}
  .summary-chip {{
    background: white; border-radius: 20px; padding: 6px 16px;
    font-size: 13px; font-weight: 600; box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    display: flex; align-items: center; gap: 6px;
  }}
  .summary-chip .dot {{ width: 8px; height: 8px; border-radius: 50%; }}
  .platform-section {{ margin-bottom: 40px; }}
  .platform-header {{
    border-radius: 12px 12px 0 0;
    padding: 14px 24px;
    display: flex; align-items: center; gap: 12px;
    color: white;
    font-weight: 700;
  }}
  .platform-icon {{ font-size: 22px; }}
  .platform-name {{ font-size: 18px; flex: 1; }}
  .profile-count {{
    background: rgba(255,255,255,0.25);
    border-radius: 12px; padding: 3px 12px; font-size: 13px;
  }}
  .platform-not-found {{
    background: white;
    border-radius: 0 0 12px 12px;
    padding: 20px 24px;
    color: #888;
    font-size: 14px;
    border: 1px solid #eee;
    border-top: none;
  }}
  .cards-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 0;
    background: white;
    border-radius: 0 0 12px 12px;
    border: 1px solid #eee;
    border-top: none;
    overflow: hidden;
  }}
  .profile-card {{
    padding: 20px;
    border-right: 1px solid #f0f0f0;
    border-bottom: 1px solid #f0f0f0;
    transition: background 0.15s;
  }}
  .profile-card:hover {{ background: #fafafa; }}
  .card-link {{
    display: flex; gap: 16px; text-decoration: none; color: inherit;
    align-items: flex-start;
  }}
  .avatar-wrap {{ flex-shrink: 0; }}
  .avatar {{
    width: 72px; height: 72px;
    border-radius: 50%;
    object-fit: cover;
    border: 2px solid #eee;
    display: block;
  }}
  .avatar-fallback {{
    width: 72px; height: 72px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    color: white; font-size: 24px; font-weight: 700;
  }}
  .card-body {{ flex: 1; min-width: 0; }}
  .card-username {{ font-weight: 700; font-size: 15px; color: #1a1a1a; }}
  .card-nickname {{ font-size: 13px; color: #555; margin-top: 2px; }}
  .card-bio {{
    font-size: 13px; color: #444; margin-top: 6px;
    line-height: 1.5; word-break: break-word;
  }}
  .card-stats {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
  .stat-badge {{
    background: #f0f0f0; border-radius: 10px;
    padding: 2px 10px; font-size: 12px; color: #444;
  }}
  .stat-badge strong {{ color: #111; }}
  .verified-badge {{
    background: #1da1f2; color: white;
    border-radius: 10px; padding: 1px 8px; font-size: 11px;
    font-weight: 600; vertical-align: middle; margin-left: 4px;
  }}
  .card-url {{
    font-size: 11px; color: #999; margin-top: 8px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }}
  .footer {{
    text-align: center; color: #aaa; font-size: 12px; margin-top: 40px;
  }}
</style>
</head>
<body>

<div class="page-header">
  <h1>Social Media Search: {query}</h1>
  <div class="meta">Generated {timestamp} &nbsp;·&nbsp; {total_profiles} profiles found across {total_platforms} platforms</div>
</div>

<div class="summary-bar">
{summary_chips}
</div>

{platform_sections}

<div class="footer">Person &amp; Business Finder · {timestamp}</div>
</body>
</html>"""


def generate_html(results: list, query: str, fetch_images: bool = True) -> str:
    """
    Generate full HTML report from social media search results.
    results: list of platform result dicts from search_all_platforms()
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    total_profiles = sum(len(r.get("profiles", [])) for r in results)
    found_platforms = [r for r in results if r.get("profiles")]
    total_platforms = len(found_platforms)

    # Summary chips
    chips = []
    for r in results:
        platform = r["platform"]
        count = len(r.get("profiles", []))
        style = PLATFORM_STYLES.get(platform, _DEFAULT_STYLE)
        color = style["color"] if count > 0 else "#ccc"
        label = f"{count} found" if count > 0 else "not found"
        chips.append(
            f'<div class="summary-chip">'
            f'<span class="dot" style="background:{color}"></span>'
            f'{platform}: <strong>{label}</strong></div>'
        )

    # Platform sections
    sections = []
    for r in results:
        platform = r["platform"]
        profiles = r.get("profiles", [])
        style = PLATFORM_STYLES.get(platform, _DEFAULT_STYLE)

        header = _platform_header_card(platform, len(profiles))

        if not profiles:
            section = header + f"""
        <div class="platform-not-found">No profiles found for this platform.</div>
    </div>"""
        else:
            # Separate profiles from posts
            pure_profiles = [p for p in profiles if not any(
                f in p.get("url", "") for f in ["/p/", "/reel/", "/status/", "/video/"]
            )]
            posts = [p for p in profiles if p not in pure_profiles]

            cards = "".join(
                _profile_card(p, platform, fetch_images=fetch_images)
                for p in pure_profiles
            )

            post_cards = ""
            if posts:
                post_cards = f"""
        <div style="padding:12px 24px;background:#f9f9f9;border-top:1px solid #eee;
                    font-size:12px;color:#888;font-weight:600;">
            RECENT ACTIVITY / POSTS
        </div>"""
                post_cards += "".join(
                    _profile_card(p, platform, fetch_images=False)
                    for p in posts[:4]
                )

            section = header + f"""
        <div class="cards-grid">
            {cards}
            {post_cards}
        </div>
    </div>"""

        sections.append(section)

    return HTML_TEMPLATE.format(
        query=query,
        timestamp=ts,
        total_profiles=total_profiles,
        total_platforms=total_platforms,
        summary_chips="\n".join(chips),
        platform_sections="\n".join(sections),
    )


def save_html(html: str, output_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
