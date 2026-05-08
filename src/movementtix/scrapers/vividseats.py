from __future__ import annotations

import logging
import re

from ..config import EVENT_IDS
from ..models import Listing, PassType
from ..pricing import detect_tier, estimate_fees
from .base import Scraper

log = logging.getLogger(__name__)

PERFORMER_URL = "https://www.vividseats.com/movement-music-festival-tickets/performer/75359"

# Vivid Seats event pages 404 if you visit /production/<id> directly —
# they require the full slug. Hardcode the verified-working full URLs
# alongside the production IDs in config.EVENT_IDS.
EVENT_URLS: dict[PassType, str] = {
    PassType.THREE_DAY: (
        "https://www.vividseats.com/movement-music-festival-tickets-"
        "detroit-hart-plaza-5-23-2026--concerts-music-festivals/production/6136478"
    ),
    PassType.SATURDAY: (
        "https://www.vividseats.com/movement-music-festival-tickets-"
        "detroit-hart-plaza-5-23-2026/production/6482557"
    ),
}


class VividSeatsScraper(Scraper):
    name = "vividseats"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        production_id = EVENT_IDS["vividseats"].get(pass_type, "")
        if not production_id:
            production_id = self._discover_production(pass_type)
        if not production_id:
            log.info("vividseats: no production for %s", pass_type.value)
            return None

        data = self._fetch_json(
            "https://www.vividseats.com/hermes/api/v1/listings",
            params={"productionId": production_id},
        )
        if not isinstance(data, dict):
            return None
        tickets = self._normalize_tickets(data.get("tickets", []) or [])
        if not tickets:
            return None

        # Sort by all-in price when available (true total user pays); fall
        # back to base + 30% guess for rows missing aip.
        def total_for(t: dict) -> float:
            return t.get("all_in_price") or (t["price"] * 1.30)
        cheapest = min(tickets, key=total_for)
        base = float(cheapest["price"])
        aip = cheapest.get("all_in_price")
        if aip is not None and aip > base:
            fees = float(aip) - base
        else:
            fees = estimate_fees(base, self.name)
        return Listing(
            site=self.name,
            pass_type=pass_type,
            base_price=base,
            fees=fees,
            quantity=int(cheapest.get("quantity", 1)),
            url=EVENT_URLS.get(pass_type) or f"https://www.vividseats.com/production/{production_id}",
            section=cheapest.get("section"),
            tier=detect_tier(cheapest.get("section")),
            raw=cheapest,
        )

    def _discover_production(self, pass_type: PassType) -> str:
        html = self._fetch_html(PERFORMER_URL, wait_ms=1500)
        if not html:
            return ""
        for _href, pid, title in re.findall(
            r'href="(/[^"]*?/production/(\d+)[^"]*)"[^>]*>([^<]+)<',
            html,
            re.IGNORECASE,
        ):
            t = title.lower()
            if pass_type is PassType.THREE_DAY and ("3 day" in t or "3-day" in t):
                return pid
            if pass_type is PassType.SATURDAY and "saturday" in t:
                return pid
            if pass_type is PassType.SUNDAY and "sunday" in t:
                return pid
            if pass_type is PassType.MONDAY and "monday" in t:
                return pid
        return ""

    @staticmethod
    def _normalize_tickets(rows: list) -> list[dict]:
        out: list[dict] = []
        for t in rows:
            price = t.get("price") or t.get("p")
            if price is None:
                continue
            base = float(price)
            # Vivid's API includes the exact all-in price per ticket in
            # `aip` / `allInPricePerTicket`. Prefer it over our 30% guess.
            aip = t.get("allInPricePerTicket") or t.get("aip")
            try:
                aip_f = float(aip) if aip is not None else None
            except (TypeError, ValueError):
                aip_f = None
            out.append(
                {
                    "price": base,
                    "all_in_price": aip_f,
                    "quantity": t.get("quantity") or t.get("q") or 1,
                    "section": t.get("section") or t.get("s"),
                }
            )
        return out
