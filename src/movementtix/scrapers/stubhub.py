from __future__ import annotations

import logging
import re

import httpx

from ..config import EVENT_IDS
from ..models import Listing, PassType
from ..pricing import estimate_fees
from .base import Scraper

log = logging.getLogger(__name__)

# Public StubHub group page; per-event IDs are discovered by scraping the
# group page once and filtering for "3 Day" / "Saturday" titles. Hard-coding
# the IDs in EVENT_IDS short-circuits the discovery step on later runs.
GROUP_URL = (
    "https://www.stubhub.com/movement-electronic-music-festival-tickets/"
    "grouping/713495/"
)


class StubHubScraper(Scraper):
    name = "stubhub"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        event_id = EVENT_IDS["stubhub"].get(pass_type, "")
        try:
            with self._client(headers={"Referer": "https://www.stubhub.com/"}) as client:
                if not event_id:
                    event_id = self._discover_event_id(client, pass_type)
                if not event_id:
                    log.info("stubhub: no event id resolved for %s", pass_type.value)
                    return None
                listings = self._fetch_listings(client, event_id)
        except httpx.HTTPError as e:
            log.warning("stubhub fetch failed: %s", e)
            return None

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

    def _discover_event_id(self, client: httpx.Client, pass_type: PassType) -> str:
        r = client.get(GROUP_URL)
        r.raise_for_status()
        html = r.text
        # Group page lists events with /event/<id> hrefs and human titles.
        candidates = re.findall(
            r'href="(/event/(\d+)[^"]*)"[^>]*>([^<]+)</a>', html, re.IGNORECASE
        )
        for _href, eid, title in candidates:
            t = title.lower()
            if pass_type is PassType.THREE_DAY and ("3 day" in t or "3-day" in t):
                return eid
            if pass_type is PassType.SATURDAY and "saturday" in t:
                return eid
        return ""

    def _fetch_listings(self, client: httpx.Client, event_id: str) -> list[dict]:
        # StubHub exposes an internal listings endpoint; URL shape may change.
        url = f"https://www.stubhub.com/_marketplace/event/{event_id}/listings"
        r = client.get(url, params={"q": 0, "qty": 1, "sort": "currentprice asc"})
        if r.status_code != 200:
            log.info("stubhub listings %s: HTTP %s", event_id, r.status_code)
            return []
        try:
            data = r.json()
        except ValueError:
            return []
        out: list[dict] = []
        for grid in data.get("listings", []) or []:
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
