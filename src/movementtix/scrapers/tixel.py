from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..models import Listing, PassType
from ..pricing import detect_tier
from .base import Scraper

log = logging.getLogger(__name__)

EVENT_URL = "https://tixel.com/us/music-tickets/2026/05/23/movement-2026"

GROUP_MAP = {
    "3-Day": PassType.THREE_DAY,
    "3 Day": PassType.THREE_DAY,
    "Weekend": PassType.THREE_DAY,
    "Saturday": PassType.SATURDAY,
    "Sunday": PassType.SUNDAY,
    "Monday": PassType.MONDAY,
}

PAYLOAD_RE = re.compile(
    r'<script[^>]*>(\[\["ShallowReactive".*?\])</script>', re.DOTALL
)


def _resolve(P: list, x: Any) -> Any:
    if isinstance(x, int):
        if 0 <= x < len(P):
            return P[x]
        return None
    return x


class TixelScraper(Scraper):
    name = "tixel"

    # Tixel embeds the full React state in the initial server-rendered
    # HTML, so we don't need a browser. Plain httpx also returns a more
    # complete listing pool: empirically the Chrome relay's session
    # (UA / cookies / fingerprint) gets served a thinned-out subset —
    # routine cycles only saw $390+ rows when sub-$340 listings were
    # actually live. Force the HTTP path so we see everything.
    @property
    def _use_relay(self) -> bool:
        return False

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        html = self._fetch_html(EVENT_URL, wait_ms=1500)
        if not html:
            return None

        payload = self._extract_payload(html)
        if not payload:
            log.warning("tixel: payload not found on page")
            return None

        listings = list(self._iter_listings(payload, pass_type))
        if not listings:
            log.info("tixel: no %s listings", pass_type.value)
            return None

        cheapest = min(listings, key=lambda x: x["total"])
        return Listing(
            site=self.name,
            pass_type=pass_type,
            base_price=float(cheapest["base"]),
            fees=float(cheapest["fee"]),
            quantity=int(cheapest.get("quantity", 1)),
            url=EVENT_URL,
            section=cheapest.get("name"),
            tier=detect_tier(cheapest.get("name"), cheapest.get("group")),
            raw=cheapest,
        )

    @staticmethod
    def _extract_payload(html: str) -> list | None:
        m = PAYLOAD_RE.search(html)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None

    @classmethod
    def _iter_listings(cls, P: list, pass_type: PassType):
        if len(P) < 5:
            return
        event = P[4] if isinstance(P[4], dict) else None
        if not event or "tickets" not in event:
            return
        tickets_meta = _resolve(P, event["tickets"])
        if not isinstance(tickets_meta, dict):
            return
        ticket_pool = _resolve(P, tickets_meta.get("available"))
        if not isinstance(ticket_pool, list):
            return

        for tref in ticket_pool:
            t = _resolve(P, tref)
            if not isinstance(t, dict):
                continue
            cat = _resolve(P, t.get("category"))
            if not isinstance(cat, dict):
                continue
            group = _resolve(P, cat.get("group"))
            mapped = GROUP_MAP.get(group) if isinstance(group, str) else None
            if mapped is not pass_type:
                continue
            base = _resolve(P, t.get("price"))
            fee = _resolve(P, t.get("buyerFee"))
            name = _resolve(P, cat.get("name"))
            if not isinstance(base, (int, float)):
                continue
            fee_val = float(fee) if isinstance(fee, (int, float)) else 0.0
            yield {
                "base": float(base),
                "fee": round(fee_val, 2),
                "total": round(float(base) + fee_val, 2),
                "name": name if isinstance(name, str) else None,
                "group": group,
                "quantity": 1,
            }
