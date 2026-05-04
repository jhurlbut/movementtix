from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class PassType(str, Enum):
    THREE_DAY = "three_day"
    SATURDAY = "saturday"

    @property
    def display(self) -> str:
        return {
            PassType.THREE_DAY: "3-Day Pass (5/23-5/25)",
            PassType.SATURDAY: "Saturday Pass (5/23)",
        }[self]


class AlertReason(str, Enum):
    UNDER_CAP = "under_cap"
    NEW_LOW = "new_low"


@dataclass(slots=True)
class Listing:
    site: str
    pass_type: PassType
    base_price: float
    fees: float
    quantity: int
    url: str
    section: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_price(self) -> float:
        return round(self.base_price + self.fees, 2)
