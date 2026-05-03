from __future__ import annotations

import logging
import re

from ..browser import open_page
from ..config import EVENT_IDS
from ..models import Listing, PassType
from ..pricing import estimate_fees
from .base import Scraper

log = logging.getLogger(__name__)

SEARCH_URL = (
    "https://www.viagogo.com/Concert-Tickets/Festivals/"
    "Movement-Electronic-Music-Festival-Tickets"
)


class ViagogoScraper(Scraper):
    name = "viagogo"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        event_id = EVENT_IDS["viagogo"].get(pass_type, "")
        url = (
            f"https://www.viagogo.com/ww/Concert-Tickets/event/{event_id}"
            if event_id
            else SEARCH_URL
        )
        price = self._fetch_with_browser(url, pass_type)
        if price is None:
            log.info("viagogo: no price extracted (likely Datadome block)")
            return None
        return Listing(
            site=self.name,
            pass_type=pass_type,
            base_price=price,
            fees=estimate_fees(price, self.name),
            quantity=1,
            url=url,
            section=None,
            raw={"event_id": event_id, "source_url": url},
        )

    def _fetch_with_browser(self, url: str, pass_type: PassType) -> float | None:
        with open_page(self.config.browser) as page:
            if page is None:
                return None
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3500)
                if pass_type is PassType.SATURDAY and "Movement" in page.url:
                    self._click_saturday(page)
                content = page.content()
            except Exception as e:
                log.info("viagogo browser fetch failed: %s", e)
                return None

        if "datadome" in content.lower() or "captcha" in content.lower():
            log.info("viagogo: hit anti-bot challenge — try CDP / user_data_dir mode")
            return None
        prices = [float(m) for m in re.findall(r'\$\s*(\d{2,4}(?:\.\d{2})?)', content)]
        return min(prices) if prices else None

    @staticmethod
    def _click_saturday(page) -> None:
        try:
            page.get_by_text("Saturday", exact=False).first.click(timeout=2500)
            page.wait_for_timeout(1500)
        except Exception:
            pass
