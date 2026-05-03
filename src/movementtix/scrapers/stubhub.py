from __future__ import annotations

import logging
import re

from ..browser import open_page
from ..config import EVENT_IDS
from ..models import Listing, PassType
from .base import Scraper

log = logging.getLogger(__name__)


class StubHubScraper(Scraper):
    """StubHub renders listings as `[data-listing-id]` cards in the page
    DOM (no longer behind /_marketplace/...). We open the event page
    through the relay and read those cards directly."""

    name = "stubhub"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        event_id = EVENT_IDS["stubhub"].get(pass_type, "")
        if not event_id:
            log.info("stubhub: no event id for %s", pass_type.value)
            return None
        url = f"https://www.stubhub.com/event/{event_id}"

        rows = self._fetch_listings_dom(url)
        if not rows:
            return None
        cheapest = min(rows, key=lambda r: r["price"])
        return Listing(
            site=self.name,
            pass_type=pass_type,
            base_price=float(cheapest["price"]),
            fees=0.0,  # StubHub displays "incl. fees" — already all-in
            quantity=int(cheapest.get("quantity", 1)),
            url=url,
            section=cheapest.get("section"),
            raw=cheapest,
        )

    def _fetch_listings_dom(self, url: str) -> list[dict]:
        if not self._use_relay:
            log.info("stubhub: relay required (set browser.cdp_url)")
            return []
        with open_page(self.config.browser, stealth=False) as page:
            if page is None:
                return []
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)
                rows = page.evaluate(
                    """
() => {
  const out = [];
  document.querySelectorAll('[data-listing-id]').forEach(el => {
    const id = el.getAttribute('data-listing-id');
    const text = (el.innerText || '').trim().replace(/\\s+/g, ' ');
    if (id && /\\$\\d/.test(text)) out.push({id, text});
  });
  return out;
}
"""
                )
            except Exception as e:
                log.info("stubhub browser fetch failed: %s", e)
                return []

        parsed: list[dict] = []
        for r in rows:
            p = self._parse_card(r["text"])
            if p:
                p["listing_id"] = r["id"]
                parsed.append(p)
        return parsed

    @staticmethod
    def _parse_card(text: str) -> dict | None:
        """e.g. 'General Admission 2 tickets Last tickets $424 incl. fees'"""
        m = re.search(r"\$\s*([\d,]+(?:\.\d{2})?)", text)
        if not m:
            return None
        price = float(m.group(1).replace(",", ""))
        qty = 1
        qm = re.search(r"(\d+)\s+tickets?", text, re.IGNORECASE)
        if qm:
            qty = int(qm.group(1))
        section = text.split(str(qty))[0].strip() if qty != 1 else text.split("$")[0].strip()
        section = re.sub(r"\s*\d+\s+tickets?", "", section).strip() or None
        return {
            "price": price,
            "quantity": qty,
            "section": section,
            "raw_text": text,
        }
