from __future__ import annotations

import logging
import re

import httpx

from ..config import EVENT_IDS
from ..models import Listing, PassType
from ..pricing import estimate_fees
from .base import Scraper

log = logging.getLogger(__name__)

PERFORMER_URL = "https://www.vividseats.com/movement-music-festival-tickets/performer/75359"


class VividSeatsScraper(Scraper):
    name = "vividseats"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        production_id = EVENT_IDS["vividseats"].get(pass_type, "")
        try:
            with self._client(headers={"Referer": "https://www.vividseats.com/"}) as client:
                if not production_id:
                    production_id = self._discover_production(client, pass_type)
                if not production_id:
                    log.info("vividseats: no production for %s", pass_type.value)
                    return None
                tickets = self._fetch_tickets(client, production_id)
        except httpx.HTTPError as e:
            log.warning("vividseats fetch failed: %s", e)
            return None

        if not tickets:
            return None
        cheapest = min(tickets, key=lambda t: t["price"])
        base = float(cheapest["price"])
        return Listing(
            site=self.name,
            pass_type=pass_type,
            base_price=base,
            fees=estimate_fees(base, self.name),
            quantity=int(cheapest.get("quantity", 1)),
            url=f"https://www.vividseats.com/production/{production_id}",
            section=cheapest.get("section"),
            raw=cheapest,
        )

    def _discover_production(self, client: httpx.Client, pass_type: PassType) -> str:
        r = client.get(PERFORMER_URL)
        r.raise_for_status()
        candidates = re.findall(
            r'href="(/[^"]*?/production/(\d+)[^"]*)"[^>]*>([^<]+)<', r.text, re.IGNORECASE
        )
        for _href, pid, title in candidates:
            t = title.lower()
            if pass_type is PassType.THREE_DAY and ("3 day" in t or "3-day" in t):
                return pid
            if pass_type is PassType.SATURDAY and "saturday" in t:
                return pid
        return ""

    def _fetch_tickets(self, client: httpx.Client, production_id: str) -> list[dict]:
        url = f"https://www.vividseats.com/hermes/api/v1/listings"
        r = client.get(url, params={"productionId": production_id})
        if r.status_code != 200:
            log.info("vividseats listings %s: HTTP %s", production_id, r.status_code)
            return []
        try:
            data = r.json()
        except ValueError:
            return []
        out: list[dict] = []
        for t in data.get("tickets", []) or []:
            price = t.get("price") or t.get("p")
            if price is None:
                continue
            out.append(
                {
                    "price": float(price),
                    "quantity": t.get("quantity") or t.get("q") or 1,
                    "section": t.get("section") or t.get("s"),
                }
            )
        return out
