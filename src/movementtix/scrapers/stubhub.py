from __future__ import annotations

import logging
import re

from ..config import EVENT_IDS
from ..models import Listing, PassType
from ..pricing import estimate_fees
from .base import Scraper

log = logging.getLogger(__name__)

GROUP_URL = (
    "https://www.stubhub.com/movement-electronic-music-festival-tickets/"
    "grouping/713495/"
)


class StubHubScraper(Scraper):
    name = "stubhub"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        event_id = EVENT_IDS["stubhub"].get(pass_type, "")
        if not event_id:
            event_id = self._discover_event_id(pass_type)
        if not event_id:
            log.info("stubhub: no event id resolved for %s", pass_type.value)
            return None

        data = self._fetch_json(
            f"https://www.stubhub.com/_marketplace/event/{event_id}/listings",
            params={"q": 0, "qty": 1, "sort": "currentprice asc"},
            headers={"Referer": "https://www.stubhub.com/"},
        )
        if not isinstance(data, dict):
            return None
        listings = self._normalize_listings(data.get("listings", []) or [])
        if not listings:
            return None

        cheapest = min(listings, key=lambda x: x["price"])
        base = float(cheapest["price"])
        return Listing(
            site=self.name,
            pass_type=pass_type,
            base_price=base,
            fees=estimate_fees(base, self.name),
            quantity=int(cheapest.get("quantity", 1)),
            url=f"https://www.stubhub.com/event/{event_id}",
            section=cheapest.get("section"),
            raw=cheapest,
        )

    def _discover_event_id(self, pass_type: PassType) -> str:
        html = self._fetch_html(GROUP_URL, wait_ms=2000)
        if not html:
            return ""
        for _href, eid, title in re.findall(
            r'href="(/event/(\d+)[^"]*)"[^>]*>([^<]+)</a>',
            html,
            re.IGNORECASE,
        ):
            t = title.lower()
            if pass_type is PassType.THREE_DAY and ("3 day" in t or "3-day" in t):
                return eid
            if pass_type is PassType.SATURDAY and "saturday" in t:
                return eid
        return ""

    @staticmethod
    def _normalize_listings(rows: list) -> list[dict]:
        out: list[dict] = []
        for grid in rows:
            price = grid.get("currentPrice", {}).get("amount") or grid.get("price")
            if price is None:
                continue
            out.append(
                {
                    "price": float(price),
                    "quantity": grid.get("quantity", 1),
                    "section": grid.get("sectionName") or grid.get("section"),
                }
            )
        return out
