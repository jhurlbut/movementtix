from __future__ import annotations

import logging
import re

from ..config import EVENT_IDS
from ..models import Listing, PassType
from ..pricing import estimate_fees
from .base import Scraper

log = logging.getLogger(__name__)


class AxsScraper(Scraper):
    """AXS event pages are heavily JS-rendered. We use Playwright when
    available; otherwise we fall back to regex over the raw HTML for the
    'starting at' price (works for the primary event but not resale)."""

    name = "axs"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        event_id = EVENT_IDS["axs"].get(pass_type, "")
        if not event_id:
            return None
        url = f"https://www.axs.com/events/{event_id}"

        price = self._fetch_with_playwright(url) or self._fetch_static(url)
        if price is None:
            log.info("axs: could not extract price for event %s", event_id)
            return None
        return Listing(
            site=self.name,
            pass_type=pass_type,
            base_price=price,
            fees=estimate_fees(price, self.name),
            quantity=1,
            url=url,
            section=None,
            raw={"event_id": event_id},
        )

    def _fetch_static(self, url: str) -> float | None:
        try:
            with self._client() as client:
                r = client.get(url)
                if r.status_code != 200:
                    return None
                m = re.search(r'"price"\s*:\s*"?\$?(\d+(?:\.\d{1,2})?)"?', r.text)
                if not m:
                    m = re.search(r'\$\s*(\d{2,4}(?:\.\d{2})?)', r.text)
                return float(m.group(1)) if m else None
        except Exception as e:
            log.info("axs static fetch failed: %s", e)
            return None

    def _fetch_with_playwright(self, url: str) -> float | None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(user_agent="Mozilla/5.0")
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                content = page.content()
                browser.close()
        except Exception as e:
            log.info("axs playwright failed: %s", e)
            return None
        m = re.search(r'\$\s*(\d{2,4}(?:\.\d{2})?)', content)
        return float(m.group(1)) if m else None
