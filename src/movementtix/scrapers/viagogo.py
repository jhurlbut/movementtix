from __future__ import annotations

import logging
import re

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
        html = self._fetch_html(url, wait_ms=3500)
        if not html:
            return None
        if "datadome" in html.lower() or "captcha" in html.lower():
            log.info("viagogo: anti-bot challenge — try a real-Chrome relay")
            return None
        prices = [float(m) for m in re.findall(r'\$\s*(\d{2,4}(?:\.\d{2})?)', html)]
        viable = [p for p in prices if p >= 30.0]
        if not viable:
            log.info("viagogo: no inline prices found")
            return None
        base = min(viable)
        return Listing(
            site=self.name,
            pass_type=pass_type,
            base_price=base,
            fees=estimate_fees(base, self.name),
            quantity=1,
            url=url,
            section=None,
            raw={"event_id": event_id, "source_url": url},
        )
