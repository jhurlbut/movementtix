from __future__ import annotations

import logging
import re

from ..config import EVENT_IDS
from ..models import Listing, PassType
from ..pricing import estimate_fees
from .base import Scraper

log = logging.getLogger(__name__)

# Viagogo has aggressive Datadome protection. The static fetch frequently
# gets a challenge page, so this scraper prefers Playwright (with stealth
# if installed) and degrades gracefully.
SEARCH_URL = "https://www.viagogo.com/Concert-Tickets/Festivals/Movement-Electronic-Music-Festival-Tickets"


class ViagogoScraper(Scraper):
    name = "viagogo"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        event_id = EVENT_IDS["viagogo"].get(pass_type, "")
        url = (
            f"https://www.viagogo.com/ww/Concert-Tickets/event/{event_id}"
            if event_id
            else SEARCH_URL
        )
        price = self._fetch_with_playwright(url, pass_type)
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

    def _fetch_with_playwright(self, url: str, pass_type: PassType) -> float | None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.info("viagogo: playwright not installed")
            return None
        try:
            from playwright_stealth import stealth_sync  # type: ignore
        except ImportError:
            stealth_sync = None  # type: ignore[assignment]

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
                    ),
                    locale="en-US",
                )
                page = ctx.new_page()
                if stealth_sync:
                    stealth_sync(page)
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3500)
                if pass_type is PassType.SATURDAY and "Movement" in page.url:
                    self._click_saturday(page)
                content = page.content()
                browser.close()
        except Exception as e:
            log.info("viagogo playwright failed: %s", e)
            return None

        if "datadome" in content.lower() or "captcha" in content.lower():
            log.info("viagogo: hit anti-bot challenge")
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
