from __future__ import annotations

import logging
import re

from ..config import EVENT_IDS
from ..models import Listing, PassType
from ..pricing import estimate_fees
from .base import Scraper

log = logging.getLogger(__name__)


class AxsScraper(Scraper):
    """AXS event pages render via JS-loaded modals. We pull whatever
    inline price hints appear in the rendered HTML and apply a $50 floor
    to skip the inevitable junk $0/$1 matches in tracking pixels."""

    name = "axs"
    MIN_PRICE = 50.0

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        event_id = EVENT_IDS["axs"].get(pass_type, "")
        if not event_id:
            return None
        url = f"https://www.axs.com/events/{event_id}"
        html = self._fetch_html(url, wait_ms=3000)
        if not html or "just a moment" in html[:2000].lower():
            log.info("axs: page not usable for event %s", event_id)
            return None
        price = self._extract(html)
        if price is None:
            log.info("axs: no inline price for event %s (likely JS modal)", event_id)
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
            # AXS inline price hints don't expose tier; left as UNKNOWN.
        )

    @classmethod
    def _extract(cls, html: str) -> float | None:
        candidates: list[float] = []
        for m in re.finditer(
            r'"(?:price|startingPrice|minPrice)"\s*:\s*"?\$?(\d+(?:\.\d{1,2})?)"?', html
        ):
            candidates.append(float(m.group(1)))
        for m in re.finditer(r'\$\s*(\d{2,4}(?:\.\d{2})?)', html):
            candidates.append(float(m.group(1)))
        viable = [p for p in candidates if p >= cls.MIN_PRICE]
        return min(viable) if viable else None
