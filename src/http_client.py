"""Polite HTTP client: robots.txt check, per-host delays, honest User-Agent.

Adapted from eu-price-monitor. Additions for this project: geo-blocks (HTTP 451, a
redirect to a "restricted" page, or a "not available in your country" notice anywhere
in the page) are blocks like captchas, and pages without a declared charset are
decoded as UTF-8 instead of requests' ISO-8859-1 default.
"""

from __future__ import annotations

import logging
import random
import re
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; iGamingBonusMonitor/0.1; B2B market data, portfolio demo)"
ROBOTS_AGENT_TOKEN = "iGamingBonusMonitor"


class FetchError(Exception):
    """A page could not be fetched (network error, bad status, robots.txt)."""


class BlockedError(FetchError):
    """The site refused us (403/429, captcha, geo-block). Never worked around."""


_CAPTCHA_URL = re.compile(r"captcha|altcha|/challenge|challenge-platform", re.IGNORECASE)
_CAPTCHA_TITLE = re.compile(
    r"captcha|not a robot|verify you are human|just a moment|attention required|are you a robot",
    re.IGNORECASE,
)
# Geo-blocks: the operator says the service is not available where the request comes from.
# Some brands serve the full page with this notice as an overlay; it still counts.
_GEO_URL = re.compile(r"restricted|geo-?block|not-available|unavailable-region", re.IGNORECASE)
_GEO_NOTICE = re.compile(
    r"(isn[’']t|is not|not) (available )?in your (country|region|area|location)"
    r"|inte tillgängligt i ditt land|ikke tilgængelige? i dit område",
    re.IGNORECASE,
)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.IGNORECASE)
_NON_TEXT = re.compile(r"<script\b.*?</script>|<style\b.*?</style>|<[^>]+>", re.IGNORECASE | re.DOTALL)


def page_title(html: str) -> str | None:
    match = _TITLE.search(html[:20000])
    return " ".join(match.group(1).split()) or None if match else None


def captcha_reason(final_url: str, html: str) -> str | None:
    """Why a 200 response is really a captcha page, or None."""
    if _CAPTCHA_URL.search(urlsplit(final_url).path):
        return f"redirected to {final_url}"
    title = page_title(html)
    if title and _CAPTCHA_TITLE.search(title):
        return f"page title {title!r}"
    return None


def geo_block_reason(final_url: str, html: str) -> str | None:
    """Why a 200 response is really a geo-block, or None."""
    parts = urlsplit(final_url)
    if _GEO_URL.search(parts.netloc.split(".")[0]) or _GEO_URL.search(parts.path):
        return f"redirected to {final_url}"
    match = _GEO_NOTICE.search(_NON_TEXT.sub(" ", html))
    if match:
        return f"page says {match.group(0)!r}"
    return None


def decode(resp: requests.Response) -> str:
    """Response text; UTF-8 (or the page's <meta charset>) when the header has no charset."""
    if "charset" not in resp.headers.get("content-type", "").lower():
        meta = _META_CHARSET.search(resp.content[:4096])
        resp.encoding = meta.group(1).decode() if meta else "utf-8"
    return resp.text


class PoliteClient:
    """requests.Session wrapper that behaves like a well-mannered crawler.

    - consults robots.txt once per host and refuses disallowed URLs;
    - waits a random 2–5 s between consecutive requests to the same host;
    - retries transient 5xx errors with backoff, but treats 403/429/451, captcha
      pages and geo-block notices (even with HTTP 200) as a block.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        delay_range: tuple[float, float] = (2.0, 5.0),
        timeout: float = 25.0,
        retries: int = 2,
    ) -> None:
        self.delay_range = delay_range
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.8,*;q=0.5",
            }
        )
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request: dict[str, float] = {}
        self.last_url: str | None = None  # final URL (after redirects) of the last page fetched

    # -- public API ---------------------------------------------------------

    def get_html(self, url: str) -> str:
        """Fetch a page and return its text, honouring robots.txt and delays."""
        if not self.allowed(url):
            raise FetchError(f"robots.txt disallows {url}")

        for attempt in range(self.retries + 1):
            self._wait_turn(url)
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise FetchError(f"{url}: {exc}") from exc
                log.warning("Network error on %s (%s), retrying", url, exc)
                continue

            if resp.status_code in (403, 429):
                challenge = " (Cloudflare challenge)" if resp.headers.get("cf-mitigated") else ""
                raise BlockedError(f"{url}: HTTP {resp.status_code}{challenge}")
            if resp.status_code == 451:
                raise BlockedError(f"{url}: geo-blocked (HTTP 451)")
            if resp.status_code >= 500 and attempt < self.retries:
                log.warning("HTTP %s on %s, retrying", resp.status_code, url)
                time.sleep(5 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise FetchError(f"{url}: HTTP {resp.status_code}")
            self.last_url = resp.url
            html = decode(resp)
            captcha = captcha_reason(resp.url, html)
            if captcha:
                raise BlockedError(f"{url}: captcha ({captcha})")
            geo = geo_block_reason(resp.url, html)
            if geo:
                raise BlockedError(f"{url}: geo-blocked ({geo})")
            return html

        raise FetchError(f"{url}: retries exhausted")  # pragma: no cover

    def allowed(self, url: str) -> bool:
        """True if robots.txt of the URL's host permits fetching it."""
        return self._robots_for(url).can_fetch(ROBOTS_AGENT_TOKEN, url)

    # -- internals ----------------------------------------------------------

    def _robots_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            parser = RobotFileParser()
            try:
                resp = self.session.get(f"{host}/robots.txt", timeout=self.timeout)
                # Per RFC 9309: 4xx means "no rules", 5xx means "assume disallowed".
                if resp.status_code >= 500:
                    parser.disallow_all = True
                else:
                    parser.parse(resp.text.splitlines() if resp.ok else [])
            except requests.RequestException:
                log.warning("robots.txt unreachable for %s, treating as disallowed", host)
                parser.disallow_all = True
            self._robots[host] = parser
            self._last_request[parts.netloc] = time.monotonic()
        return self._robots[host]

    def _wait_turn(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last_request.get(host)
        if last is not None:
            delay = random.uniform(*self.delay_range)
            remaining = delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request[host] = time.monotonic()
