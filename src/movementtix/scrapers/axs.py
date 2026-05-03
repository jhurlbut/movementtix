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

    MIN_PRICE = 50.0  # skip junk $0 matches; face value pass is $169+

    def _fetch_static(self, url: str) -> float | None:
        try:
            with self._client() as client:
                r = client.get(url)
                if r.status_code != 200:
                    return None
                return self._extract(r.text)
        except Exception as e:
            log.info("axs static fetch failed: %s", e)
            return None

    def _fetch_with_browser(self, url: str) -> float | None:
        with open_page(self.config.browser) as page:
            if page is None:
                return None
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Wait up to 15s for the Cloudflare interstitial to resolve.
                for _ in range(15):
                    title = (page.title() or "").lower()
                    if "just a moment" not in title and title:
                        break
                    page.wait_for_timeout(1000)
                page.wait_for_timeout(1500)
                content = page.content()
            except Exception as e:
                log.info("axs browser fetch failed: %s", e)
                return None
        if "just a moment" in (content[:2000].lower()):
            log.info("axs: Cloudflare challenge did not clear")
            return None
        return self._extract(content)

    @classmethod
    def _extract(cls, html: str) -> float | None:
        candidates: list[float] = []
        for m in re.finditer(r'"(?:price|startingPrice|minPrice)"\s*:\s*"?\$?(\d+(?:\.\d{1,2})?)"?', html):
            candidates.append(float(m.group(1)))
        for m in re.finditer(r'\$\s*(\d{2,4}(?:\.\d{2})?)', html):
            candidates.append(float(m.group(1)))
        viable = [p for p in candidates if p >= cls.MIN_PRICE]
        return min(viable) if viable else None
