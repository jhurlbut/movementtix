from __future__ import annotations

import json
import logging
import random
from abc import ABC, abstractmethod

import httpx

from ..browser import open_page
from ..config import Config
from ..models import Listing, PassType

log = logging.getLogger(__name__)

UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


def random_ua() -> str:
    return random.choice(UA_POOL)


class Scraper(ABC):
    name: str = "base"

    def __init__(self, config: Config):
        self.config = config

    @abstractmethod
    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        """Return the cheapest listing for this pass type, or None.

        Implementations should swallow recoverable network errors and log
        them; raise only on programmer error.
        """

    # ------------------------------------------------------------------
    # Shared HTTP helpers — auto-route through the Chrome relay when
    # browser.cdp_url or browser.user_data_dir is configured.
    # ------------------------------------------------------------------

    @property
    def _use_relay(self) -> bool:
        b = self.config.browser
        return bool(b.cdp_url or b.user_data_dir)

    def _fetch_html(self, url: str, *, wait_ms: int = 2000,
                    wait_for_selector: str | None = None) -> str | None:
        """GET a page and return its rendered HTML."""
        if self._use_relay:
            return self._fetch_html_via_browser(url, wait_ms, wait_for_selector)
        return self._fetch_html_via_http(url)

    def _fetch_json(self, url: str, *, params: dict | None = None,
                    headers: dict | None = None) -> dict | list | None:
        """GET a JSON endpoint."""
        if self._use_relay:
            return self._fetch_json_via_browser(url, params, headers)
        return self._fetch_json_via_http(url, params, headers)

    # ---- httpx path ---------------------------------------------------

    def _client(self, **kwargs) -> httpx.Client:
        headers = {
            "User-Agent": random_ua(),
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        headers.update(kwargs.pop("headers", {}))
        return httpx.Client(timeout=20, headers=headers, follow_redirects=True, **kwargs)

    def _fetch_html_via_http(self, url: str) -> str | None:
        try:
            with self._client() as client:
                r = client.get(url)
                if r.status_code != 200:
                    log.info("%s: HTTP %s for %s", self.name, r.status_code, url)
                    return None
                return r.text
        except httpx.HTTPError as e:
            log.warning("%s httpx fetch failed: %s", self.name, e)
            return None

    def _fetch_json_via_http(self, url: str, params: dict | None,
                             headers: dict | None) -> dict | list | None:
        try:
            extra = {"headers": headers} if headers else {}
            with self._client(**extra) as client:
                r = client.get(url, params=params)
                if r.status_code != 200:
                    log.info("%s: HTTP %s for %s", self.name, r.status_code, url)
                    return None
                return r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("%s httpx json failed: %s", self.name, e)
            return None

    # ---- relay path ---------------------------------------------------

    def _fetch_html_via_browser(self, url: str, wait_ms: int,
                                wait_for_selector: str | None) -> str | None:
        with open_page(self.config.browser, stealth=False) as page:
            if page is None:
                return None
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Auto-clear Cloudflare interstitial if present.
                for _ in range(15):
                    title = (page.title() or "").lower()
                    if title and "just a moment" not in title:
                        break
                    page.wait_for_timeout(1000)
                if wait_for_selector:
                    try:
                        page.wait_for_selector(wait_for_selector, timeout=8000)
                    except Exception:
                        pass
                if wait_ms:
                    page.wait_for_timeout(wait_ms)
                return page.content()
            except Exception as e:
                log.info("%s browser fetch failed: %s", self.name, e)
                return None

    def _fetch_json_via_browser(self, url: str, params: dict | None,
                                headers: dict | None) -> dict | list | None:
        """Use Playwright's APIRequestContext so the request rides through
        the relay's network stack (cookies, headers, anti-bot tokens)
        without a full DOM render."""
        with open_page(self.config.browser, stealth=False) as page:
            if page is None:
                return None
            try:
                req_ctx = page.context.request
                response = req_ctx.get(
                    url,
                    params=params or {},
                    headers=headers or {},
                    timeout=20000,
                )
                if not response.ok:
                    log.info("%s: relay HTTP %s for %s", self.name, response.status, url)
                    return None
                text = response.text()
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    log.info("%s: non-JSON response from %s", self.name, url)
                    return None
            except Exception as e:
                log.info("%s relay json failed: %s", self.name, e)
                return None
