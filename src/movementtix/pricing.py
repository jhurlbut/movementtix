from __future__ import annotations

from .config import Caps
from .models import AlertReason, Listing


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
