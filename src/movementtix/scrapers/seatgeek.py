from __future__ import annotations

import logging

import httpx

from ..models import Listing, PassType
from ..pricing import estimate_fees
from .base import Scraper

log = logging.getLogger(__name__)

# SeatGeek event title heuristics for filtering.
TITLE_HINTS = {
    PassType.THREE_DAY: ("3-day", "3 day", "three day", "3day"),
    PassType.SATURDAY: ("saturday",),
}


class SeatGeekScraper(Scraper):
    name = "seatgeek"
    BASE = "https://api.seatgeek.com/2"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        cid = self.config.seatgeek_client_id
        if not cid:
            log.info("seatgeek: SEATGEEK_CLIENT_ID not set; skipping")
            return None

        try:
            with self._client() as client:
                events = self._search_events(client, cid)
        except httpx.HTTPError as e:
            log.warning("seatgeek search failed: %s", e)
            return None

        match = self._pick_event(events, pass_type)
        if not match:
            log.info("seatgeek: no event matched %s", pass_type.value)
            return None

        stats = match.get("stats") or {}
        lowest = stats.get("lowest_price")
        if lowest is None:
            log.info("seatgeek: event %s has no listings", match.get("id"))
            return None

        base = float(lowest)
        return Listing(
            site=self.name,
            pass_type=pass_type,
            base_price=base,
            fees=estimate_fees(base, self.name),
            quantity=int(stats.get("listing_count") or 1),
            url=match.get("url", ""),
            section=None,
            raw={"event_id": match.get("id"), "stats": stats, "title": match.get("title")},
        )

    def _search_events(self, client: httpx.Client, cid: str) -> list[dict]:
        params = {
            "client_id": cid,
            "q": "Movement Music Festival",
            "venue.city": "Detroit",
            "datetime_utc.gte": "2026-05-23",
            "datetime_utc.lte": "2026-05-26",
            "per_page": 25,
        }
        r = client.get(f"{self.BASE}/events", params=params)
        r.raise_for_status()
        return r.json().get("events", [])

    @staticmethod
    def _pick_event(events: list[dict], pass_type: PassType) -> dict | None:
        hints = TITLE_HINTS[pass_type]
        for ev in events:
            title = (ev.get("title") or "").lower()
            if any(h in title for h in hints):
                return ev
        return None
