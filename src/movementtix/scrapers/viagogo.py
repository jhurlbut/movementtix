from __future__ import annotations

import logging
import re

from ..browser import open_page
from ..config import EVENT_IDS
from ..models import Listing, PassType
from ..pricing import detect_tier
from .base import Scraper

log = logging.getLogger(__name__)

# Movement landing page on Viagogo. Each event detail lives at
# `<EVENT_LIST_URL>/E-<id>` and shares its numeric ID with StubHub
# (Viagogo and StubHub are sister sites under the same parent).
EVENT_LIST_URL = (
    "https://www.viagogo.com/Festival-Tickets/International-Festivals/"
    "Movement-Electronic-Music-Festival-Tickets"
)


class ViagogoScraper(Scraper):
    """Viagogo renders inventory the same way StubHub does — `[data-listing-id]`
    cards inside the event page DOM, gated behind AWS WAF that fingerprints
    headless browsers. We use `force_headed=True` to launch the full Chromium
    binary under a real (or virtual) display so the WAF challenge passes."""

    name = "viagogo"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        event_id = EVENT_IDS["viagogo"].get(pass_type, "")
        if not event_id:
            log.info("viagogo: no event id for %s", pass_type.value)
            return None
        url = f"{EVENT_LIST_URL}/E-{event_id}"

        rows = self._fetch_listings_dom(url)
        if not rows:
            return None
        cheapest = min(rows, key=lambda r: r["price"])
        return Listing(
            site=self.name,
            pass_type=pass_type,
            base_price=float(cheapest["price"]),
            fees=0.0,  # Viagogo also displays "incl. fees" — already all-in
            quantity=int(cheapest.get("quantity", 1)),
            url=url,
            section=cheapest.get("section"),
            tier=detect_tier(cheapest.get("section"), cheapest.get("raw_text")),
            raw=cheapest,
        )

    def _fetch_listings_dom(self, url: str) -> list[dict]:
        with open_page(self.config.browser, stealth=True, force_headed=True) as page:
            if page is None:
                return []
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(8000)
                rows = page.evaluate(
                    """
() => {
  const out = [];
  document.querySelectorAll('[data-listing-id]').forEach(el => {
    const id = el.getAttribute('data-listing-id');
    const text = (el.innerText || '').trim().replace(/\\s+/g, ' ');
    if (id && /\\$\\d/.test(text)) out.push({id, text});
  });
  const c = document.querySelector('[data-testid="listings-container"]');
  const placeholders = c ? Array.from(c.children).filter(el =>
    el.children.length === 0 && (el.innerText || '').trim() === ''
  ).length : 0;
  const showing = (document.body.innerText.match(/Showing\\s+\\d+\\s+of\\s+\\d+/) || [''])[0];
  return {rows: out, container_present: !!c, empty_placeholders: placeholders, showing};
}
"""
                )
            except Exception as e:
                log.info("viagogo browser fetch failed: %s", e)
                return []

        parsed_rows = rows.get("rows", []) if isinstance(rows, dict) else (rows or [])
        if isinstance(rows, dict) and not parsed_rows and rows.get("container_present"):
            log.warning(
                "viagogo: WAF/bot block — listings-container has %d empty"
                " placeholders, page reports %r",
                rows.get("empty_placeholders") or 0,
                rows.get("showing") or "(no count)",
            )

        parsed: list[dict] = []
        for r in parsed_rows:
            p = self._parse_card(r["text"])
            if p:
                p["listing_id"] = r["id"]
                parsed.append(p)
        return parsed

    @staticmethod
    def _parse_card(text: str) -> dict | None:
        """e.g. 'General Admission 2 tickets Best price $364 incl. fees'"""
        m = re.search(r"\$\s*([\d,]+(?:\.\d{2})?)", text)
        if not m:
            return None
        price = float(m.group(1).replace(",", ""))
        qty = 1
        qm = re.search(r"(\d+)\s+tickets?", text, re.IGNORECASE)
        if qm:
            qty = int(qm.group(1))
        section = text.split("$")[0].strip()
        section = re.sub(r"\s*\d+\s+tickets?", "", section, flags=re.IGNORECASE).strip()
        section = re.sub(r"\b(Best price|Last tickets)\b", "", section, flags=re.IGNORECASE).strip()
        section = section or None
        return {
            "price": price,
            "quantity": qty,
            "section": section,
            "raw_text": text,
        }
