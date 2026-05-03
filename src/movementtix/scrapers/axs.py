from __future__ import annotations

import logging
import re

from ..browser import open_page
from ..config import EVENT_IDS
from ..models import Listing, PassType
from ..pricing import estimate_fees
from .base import Scraper

log = logging.getLogger(__name__)


class AxsScraper(Scraper):
    """AXS event pages are heavily JS-rendered. We use the configured
    browser (CDP / persistent profile / headless) and fall back to a
    static fetch for the 'starting at' price."""

    name = "axs"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        event_id = EVENT_IDS["axs"].get(pass_type, "")
        if not event_id:
            return None
        url = f"https://www.axs.com/events/{event_id}"

        price = self._fetch_with_browser(url) or self._fetch_static(url)
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

    def _fetch_with_browser(self, url: str) -> float | None:
        with open_page(self.config.browser) as page:
            if page is None:
                return None
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                content = page.content()
            except Exception as e:
                log.info("axs browser fetch failed: %s", e)
                return None
        prices = [float(m) for m in re.findall(r'\$\s*(\d{2,4}(?:\.\d{2})?)', content)]
        return min(prices) if prices else None
