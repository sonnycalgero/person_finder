"""
http_client.py — Shared HTTP fetch layer with UA rotation, retry, thin-content
detection, and optional Playwright + stealth escalation.

All tools in this project should route HTTP requests through this module:
  - fetch()     → web pages (full pipeline: retry, UA, content check, Playwright)
  - fetch_api() → API endpoints (retry + UA only, no browser)

Playwright is optional. If not installed, everything works as before — the
browser escalation path is simply skipped.
"""

import atexit
import random
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ── UA rotation ──────────────────────────────────────────────────────────────

UA_POOL = [
    # Chrome 124–126 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome 124–126 on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox 126–127 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Firefox 126–127 on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Edge 126 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    # Safari 17 on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


def get_random_ua() -> str:
    """Return a random User-Agent string from the pool."""
    return random.choice(UA_POOL)


def get_headers(ua: str | None = None) -> dict:
    """Return standard browser-like headers with a rotated UA."""
    return {
        "User-Agent": ua or get_random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }


# ── Retry logic ──────────────────────────────────────────────────────────────

RETRY_CODES = {429, 502, 503, 520, 521, 522, 523, 524}
MAX_RETRIES = 3
BACKOFF_BASE = 2  # delays: 2s, 4s, 8s


def _request_with_retry(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: int = 30,
    max_retries: int = MAX_RETRIES,
) -> requests.Response:
    """Wrap requests.get with exponential backoff on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            hdrs = headers if headers is not None else get_headers()
            resp = requests.get(
                url, headers=hdrs, params=params,
                timeout=timeout, allow_redirects=True,
            )
            if resp.status_code in RETRY_CODES and attempt < max_retries:
                delay = BACKOFF_BASE ** (attempt + 1)
                print(
                    f"  [retry {attempt + 1}/{max_retries}: HTTP {resp.status_code}, "
                    f"waiting {delay}s]",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt < max_retries:
                delay = BACKOFF_BASE ** (attempt + 1)
                print(
                    f"  [retry {attempt + 1}/{max_retries}: {type(e).__name__}, "
                    f"waiting {delay}s]",
                    file=sys.stderr,
                )
                time.sleep(delay)
            else:
                raise
        except requests.HTTPError:
            raise
    raise last_exc  # type: ignore[misc]


# ── Thin-content detection ───────────────────────────────────────────────────

@dataclass
class ContentDiagnosis:
    is_thin: bool
    reason: str   # "ok" | "redirect_to_homepage" | "bot_block" | "too_short"
    detail: str   # human-readable explanation


_BOT_BLOCK_SIGNATURES = [
    "cf-browser-verification",
    "cf_chl_opt",
    "Checking your browser",
    "g-recaptcha",
    "h-captcha",
    "px-captcha",          # PerimeterX
    "datadome",            # DataDome
    "akamai-bot-manager",  # Akamai
]

_BOT_BLOCK_TITLES = [
    "access denied",
    "just a moment",
    "verify you are human",
    "attention required",
    "please wait",
    "security check",
    "pardon our interruption",
    "are you a robot",
]


def diagnose_content(
    html: str, original_url: str, final_url: str
) -> ContentDiagnosis:
    """Check whether fetched HTML is actually useful content."""

    orig_path = urlparse(original_url).path.rstrip("/")
    final_path = urlparse(final_url).path.rstrip("/")

    # 1. Homepage redirect: had a real path, ended up at root
    if orig_path and len(orig_path) > 1 and (not final_path or final_path == ""):
        return ContentDiagnosis(
            is_thin=True,
            reason="redirect_to_homepage",
            detail=f"redirected from {original_url} to homepage {final_url}",
        )

    html_lower = html.lower()

    # 2. Bot-block signatures in HTML body
    for sig in _BOT_BLOCK_SIGNATURES:
        if sig.lower() in html_lower:
            return ContentDiagnosis(
                is_thin=True,
                reason="bot_block",
                detail=f"bot-block signature detected: '{sig}'",
            )

    # 3. Suspicious <title>
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_lower, re.DOTALL)
    if title_match:
        title_text = title_match.group(1).strip()
        for marker in _BOT_BLOCK_TITLES:
            if marker in title_text:
                return ContentDiagnosis(
                    is_thin=True,
                    reason="bot_block",
                    detail=f"bot-block title detected: '{title_text[:80]}'",
                )

    # 4. Visible-text length check
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        visible_text = soup.get_text(separator=" ", strip=True)
        if len(visible_text) < 200:
            return ContentDiagnosis(
                is_thin=True,
                reason="too_short",
                detail=f"only {len(visible_text)} chars of visible text",
            )
    except Exception:
        pass

    return ContentDiagnosis(is_thin=False, reason="ok", detail="")


# ── Playwright integration (optional) ────────────────────────────────────────

_browser = None
_playwright_instance = None


def is_playwright_available() -> bool:
    """Check if Playwright + stealth are installed."""
    try:
        import playwright.sync_api  # noqa: F401
        from playwright_stealth import Stealth  # noqa: F401
        return True
    except ImportError:
        return False


def _ensure_browser():
    """Lazily start a Chromium browser. Reused across fetches."""
    global _browser, _playwright_instance
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _playwright_instance = sync_playwright().start()
        _browser = _playwright_instance.chromium.launch(headless=True)
    return _browser


def cleanup_browser() -> None:
    """Close the browser and Playwright instance. Suppresses asyncio/greenlet
    noise that Playwright emits during interpreter shutdown."""
    global _browser, _playwright_instance
    if _browser:
        try:
            _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright_instance:
        try:
            _playwright_instance.stop()
        except Exception:
            pass
        _playwright_instance = None


def _quiet_cleanup():
    """atexit wrapper that suppresses stderr noise from Playwright shutdown."""
    import io
    import contextlib
    with contextlib.redirect_stderr(io.StringIO()):
        cleanup_browser()


atexit.register(_quiet_cleanup)


def _fetch_with_playwright(url: str, timeout: int = 30) -> tuple[str, str]:
    """Fetch a page using a stealth Chromium browser. Returns (html, final_url)."""
    from playwright_stealth import Stealth

    stealth = Stealth()
    browser = _ensure_browser()
    context = browser.new_context(
        locale="en-US",
        timezone_id="America/New_York",
    )
    stealth.apply_stealth_sync(context)
    page = context.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
        html = page.content()
        final_url = page.url
        return html, final_url
    finally:
        context.close()


# ── Public API ───────────────────────────────────────────────────────────────

@dataclass
class FetchResult:
    html: str
    final_url: str
    method_used: str  # "requests" | "playwright" | "proxy"
    content_diagnosis: ContentDiagnosis


def fetch(
    url: str,
    *,
    use_proxy_fn: Callable[[str], bool] | None = None,
    proxy_fetch_fn: Callable[[str], tuple[str, str]] | None = None,
    allow_browser: bool = True,
    timeout: int = 30,
) -> FetchResult:
    """Fetch a web page with the full pipeline.

    Routing:
      1. If use_proxy_fn(url) and proxy_fetch_fn provided → proxy
      2. Otherwise → requests with rotated UA
      3. If result is thin AND Playwright available → escalate to browser

    Args:
        url: The page URL.
        use_proxy_fn: Callable that returns True if this URL needs a proxy.
        proxy_fetch_fn: Callable that fetches via proxy, returns (html, final_url).
        allow_browser: Whether to escalate to Playwright on thin content.
        timeout: Request timeout in seconds.
    """
    html = ""
    final_url = url
    method = "requests"

    # ── Path 1: Proxy ────────────────────────────────────────────────────
    if use_proxy_fn and proxy_fetch_fn and use_proxy_fn(url):
        try:
            html, final_url = proxy_fetch_fn(url)
            method = "proxy"
        except Exception as e:
            print(f"  [proxy failed: {e}]", file=sys.stderr)
            # Fall through to try requests or Playwright
            html = ""

    # ── Path 2: Direct requests ──────────────────────────────────────────
    if not html:
        try:
            resp = _request_with_retry(url, timeout=timeout)
            html = resp.text
            final_url = resp.url
            method = "requests"
        except Exception as e:
            # If requests also fails and Playwright is available, try that
            if allow_browser and is_playwright_available():
                print(
                    f"  [requests failed: {e} — escalating to Playwright]",
                    file=sys.stderr,
                )
                try:
                    html, final_url = _fetch_with_playwright(url, timeout=timeout)
                    method = "playwright"
                except Exception as pw_e:
                    # Everything failed
                    raise Exception(
                        f"all fetch methods failed — "
                        f"requests: {e}, playwright: {pw_e}"
                    ) from pw_e
            else:
                raise

    # ── Content diagnosis ────────────────────────────────────────────────
    diagnosis = diagnose_content(html, url, final_url)

    # ── Playwright escalation on thin content ────────────────────────────
    # Only escalate for JS-rendered shells and homepage redirects.
    # Skip bot_block — DataDome/Cloudflare block headless browsers just as
    # effectively as requests, so Playwright just wastes 30s.
    _playwright_worth_trying = (
        diagnosis.is_thin
        and diagnosis.reason != "bot_block"
        and allow_browser
        and is_playwright_available()
        and method != "playwright"
    )
    if _playwright_worth_trying:
        print(
            f"  [thin content via {method}: {diagnosis.detail} "
            f"— escalating to Playwright]",
            file=sys.stderr,
        )
        try:
            pw_html, pw_url = _fetch_with_playwright(url, timeout=timeout)
            pw_diagnosis = diagnose_content(pw_html, url, pw_url)
            # Use Playwright result even if still thin (it's our best shot)
            html = pw_html
            final_url = pw_url
            method = "playwright"
            diagnosis = pw_diagnosis
        except Exception as e:
            print(f"  [Playwright escalation failed: {e}]", file=sys.stderr)
            # Keep the original (thin) result
    elif diagnosis.is_thin and diagnosis.reason == "bot_block":
        print(
            f"  [WARNING: {diagnosis.detail} — bot-protection active, "
            f"Playwright escalation skipped]",
            file=sys.stderr,
        )

    return FetchResult(
        html=html,
        final_url=final_url,
        method_used=method,
        content_diagnosis=diagnosis,
    )


def fetch_api(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: int = 15,
    max_retries: int = MAX_RETRIES,
) -> requests.Response:
    """Fetch an API endpoint with retry and UA rotation. No Playwright, no
    content diagnosis — just a reliable requests.get wrapper for API calls."""
    return _request_with_retry(
        url, headers=headers, params=params,
        timeout=timeout, max_retries=max_retries,
    )
