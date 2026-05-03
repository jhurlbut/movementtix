from __future__ import annotations

import logging
import re

import httpx

from ..models import Listing, PassType
from ..pricing import estimate_fees
from .base import Scraper

log = logging.getLogger(__name__)

# Tixel groups all Movement listings under a single search slug. We filter
# the resulting cards by title.
SEARCH_URL = "https://tixel.com/au/search?q=movement+festival+detroit+2026"


class TixelScraper(Scraper):
    name = "tixel"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        try:
            with self._client(headers={"Referer": "https://tixel.com/"}) as client:
                cards = self._search(client)
                if not cards:
                    return None
                target = self._pick(cards, pass_type)
                if not target:
                    return None
                listings = self._listings_for(client, target["href"])
        except httpx.HTTPError as e:
            log.warning("tixel fetch failed: %s", e)
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
            url=f"https://tixel.com{target['href']}",
            section=cheapest.get("section"),
            raw=cheapest,
        )

    def _search(self, client: httpx.Client) -> list[dict]:
        r = client.get(SEARCH_URL)
        r.raise_for_status()
        # Each search result links into /au/<event-slug>
        matches = re.findall(
            r'href="(/(?:au|us)/[^"]*movement[^"]*)"[^>]*>([^<]{4,200})</',
            r.text,
            re.IGNORECASE,
        )
        return [{"href": h, "title": t.strip()} for h, t in matches]

    @staticmethod
    def _pick(cards: list[dict], pass_type: PassType) -> dict | None:
        for c in cards:
            t = c["title"].lower()
            if pass_type is PassType.THREE_DAY and ("3 day" in t or "3-day" in t or "weekend" in t):
                return c
            if pass_type is PassType.SATURDAY and "saturday" in t:
                return c
        return None

    def _listings_for(self, client: httpx.Client, href: str) -> list[dict]:
        r = client.get(f"https://tixel.com{href}")
        if r.status_code != 200:
            return []
        # Tixel renders a JSON island with all listings.
        m = re.search(r'"listings":(\[.*?\])', r.text)
        if not m:
            return []
        import json
        try:
            raw = json.loads(m.group(1))
        except ValueError:
            return []
        out: list[dict] = []
        for item in raw:
            price = item.get("price") or item.get("totalPrice")
            if not price:
                continue
            out.append(
                {
                    "price": float(price) / (100 if price > 1000 else 1),
                    "quantity": item.get("quantity", 1),
                    "section": item.get("section"),
                }
            )
        return out
