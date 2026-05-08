"""Eventim.us — Movement's *primary* box-office issuer (Tixel and the
StubHub family are licensed resale partners). Worth tracking because
primary inventory is often the cheapest source: face value plus a
small service fee, while resale prices vary with secondary demand."""
from __future__ import annotations

import logging
import re

from ..browser import open_page
from ..models import Listing, PassType, Tier
from ..pricing import detect_tier
from .base import Scraper

log = logging.getLogger(__name__)

EVENT_URL = "https://www.eventim.us/event/tickets/655986"

# Heading patterns we extract from the page body. Each pass we care
# about is rendered as three adjacent lines:
#   "GA SATURDAY (WAVE 2)(All Ages)"
#   "$227.45"
#   "($189.00face value + $38.45service fee)"
# The (WAVE N) tier rolls forward as inventory sells through.
_HEADING_PATTERNS: dict[PassType, re.Pattern] = {
    PassType.THREE_DAY: re.compile(r"^(GA|VIP)\s+3[\s-]?DAY\b", re.IGNORECASE),
    PassType.SATURDAY: re.compile(r"^(GA|VIP)\s+SATURDAY\b", re.IGNORECASE),
    PassType.SUNDAY: re.compile(r"^(GA|VIP)\s+SUNDAY\b", re.IGNORECASE),
    PassType.MONDAY: re.compile(r"^(GA|VIP)\s+MONDAY\b", re.IGNORECASE),
}

_PRICE_LINE = re.compile(r"^\$\s*([\d,]+(?:\.\d{2})?)\s*$")
_FEE_LINE = re.compile(
    r"\(\s*\$([\d,]+(?:\.\d{2})?)\s*face\s*value\s*\+\s*\$([\d,]+(?:\.\d{2})?)\s*service\s*fee",
    re.IGNORECASE,
)


class EventimScraper(Scraper):
    name = "eventim"

    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        pat = _HEADING_PATTERNS.get(pass_type)
        if pat is None:
            return None
        lines = self._fetch_lines()
        if not lines:
            return None
        parsed = self._parse(lines, pat)
        if not parsed:
            log.info("eventim: no GA listing found for %s", pass_type.value)
            return None
        return Listing(
            site=self.name,
            pass_type=pass_type,
            base_price=parsed["face"],
            fees=parsed["fees"],
            quantity=1,
            url=EVENT_URL,
            section=parsed.get("section"),
            tier=detect_tier(parsed.get("section")),
            raw=parsed,
        )

    def _fetch_lines(self) -> list[str]:
        # Akamai blocks both plain httpx (403) and headless Chromium
        # (truncated body), so we use the full Chromium + headed display
        # path — same approach that defeats StubHub's WAF.
        with open_page(self.config.browser, stealth=True, force_headed=True) as page:
            if page is None:
                return []
            try:
                page.goto(EVENT_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(8000)
                text = page.evaluate("() => document.body.innerText") or ""
            except Exception as e:
                log.info("eventim browser fetch failed: %s", e)
                return []
        return [ln.strip() for ln in text.split("\n") if ln.strip()]

    @staticmethod
    def _parse(lines: list[str], heading_pat: re.Pattern) -> dict | None:
        # Walk lines looking for the heading; the next $-prefixed line is
        # the displayed total, and the line after it is the breakdown.
        for i, ln in enumerate(lines):
            if not heading_pat.search(ln):
                continue
            # SOLD OUT rows are heading-only (no following price). Skip.
            total = None
            face = None
            fees = None
            for j in range(i + 1, min(i + 6, len(lines))):
                if total is None:
                    pm = _PRICE_LINE.match(lines[j])
                    if pm:
                        total = float(pm.group(1).replace(",", ""))
                        continue
                fm = _FEE_LINE.search(lines[j])
                if fm:
                    face = float(fm.group(1).replace(",", ""))
                    fees = float(fm.group(2).replace(",", ""))
                    break
                # Stop if we hit the next heading without finding a price
                if heading_pat.search(lines[j]) or re.match(
                    r"^(GA|VIP)\s", lines[j]
                ):
                    break
            if total is None:
                continue
            # Prefer the explicit face/fees breakdown when we have it
            if face is not None and fees is not None:
                return {
                    "face": face,
                    "fees": fees,
                    "total": face + fees,
                    "section": ln,
                }
            return {
                "face": total,
                "fees": 0.0,
                "total": total,
                "section": ln,
            }
        return None
