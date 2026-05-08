from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class PassType(str, Enum):
    THREE_DAY = "three_day"
    SATURDAY = "saturday"
    SUNDAY = "sunday"
    MONDAY = "monday"

    @property
    def display(self) -> str:
        return {
            PassType.THREE_DAY: "3-Day Pass (5/23-5/25)",
            PassType.SATURDAY: "Saturday Pass (5/23)",
            PassType.SUNDAY: "Sunday Pass (5/24)",
            PassType.MONDAY: "Monday Pass (5/25)",
        }[self]

    @classmethod
    def parse(cls, raw: str) -> "PassType | None":
        """Lenient parse for user-typed Telegram args."""
        s = raw.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "3day": cls.THREE_DAY, "three_day": cls.THREE_DAY,
            "3_day": cls.THREE_DAY, "weekend": cls.THREE_DAY,
            "sat": cls.SATURDAY, "saturday": cls.SATURDAY,
            "sun": cls.SUNDAY, "sunday": cls.SUNDAY,
            "mon": cls.MONDAY, "monday": cls.MONDAY,
        }
        return aliases.get(s)


class Tier(str, Enum):
    GA = "ga"
    VIP = "vip"
    UNKNOWN = "unknown"

    @property
    def display(self) -> str:
        return {Tier.GA: "GA", Tier.VIP: "VIP", Tier.UNKNOWN: "?"}[self]

    @classmethod
    def parse(cls, raw: str) -> "Tier | None":
        s = raw.strip().lower()
        if s in ("ga", "general", "general_admission", "ga_admission"):
            return cls.GA
        if s in ("vip",):
            return cls.VIP
        if s in ("any", "all", "either"):
            return None  # caller treats None as "no tier filter"
        return None


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
    tier: Tier = Tier.UNKNOWN
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_price(self) -> float:
        return round(self.base_price + self.fees, 2)
