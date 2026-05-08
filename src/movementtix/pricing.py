from __future__ import annotations

import re

from .config import Caps
from .models import AlertReason, Listing, Tier

_VIP_RE = re.compile(r"\b(vip|cabana|premium|loft|hospitality)\b", re.I)
_GA_RE = re.compile(r"\b(ga|general\s*admission|general)\b", re.I)


def detect_tier(*texts: str | None) -> Tier:
    """Classify a listing's tier from any text fields the scraper has
    (section name, group label, raw category, listing title). Returns
    Tier.VIP or Tier.GA on a clean keyword match, otherwise UNKNOWN.

    VIP wins ties — many GA listings use "GA + VIP add-on" phrasing
    where both appear; treating those as VIP is safer (better to
    over-tag VIP than mis-route a VIP-priced ticket as GA)."""
    haystack = " ".join(t for t in texts if t)
    if not haystack:
        return Tier.UNKNOWN
    if _VIP_RE.search(haystack):
        return Tier.VIP
    if _GA_RE.search(haystack):
        return Tier.GA
    return Tier.UNKNOWN


def estimate_fees(base_price: float, site: str) -> float:
    """Return an estimated fee for sites that don't expose all-in pricing.

    Numbers are rough industry averages; tune per site as you observe real
    checkouts. Always biased slightly high so we don't over-trigger alerts.
    """
    rates = {
        "stubhub": 0.28,
        "viagogo": 0.32,
        "vividseats": 0.30,
        "axs": 0.18,
        "tixel": 0.10,
        "seatgeek": 0.25,
    }
    return round(base_price * rates.get(site, 0.25), 2)


def should_alert(
    current: Listing,
    prior_min: float | None,
    caps: Caps,
) -> AlertReason | None:
    cap = caps.for_pass(current.pass_type)
    if current.total_price <= cap:
        return AlertReason.UNDER_CAP
    if prior_min is None or current.total_price < prior_min:
        return AlertReason.NEW_LOW
    return None
