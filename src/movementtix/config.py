from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from .models import PassType


class Caps(BaseModel):
    three_day: float = 300.0
    saturday: float = 150.0

    def for_pass(self, pt: PassType) -> float:
        return self.three_day if pt is PassType.THREE_DAY else self.saturday


class PollSeconds(BaseModel):
    min: int = 300
    max: int = 600


class Sites(BaseModel):
    tixel: bool = True
    axs: bool = True
    stubhub: bool = True
    viagogo: bool = True
    vividseats: bool = True
    seatgeek: bool = True

    def enabled(self) -> list[str]:
        return [name for name, on in self.model_dump().items() if on]


class Config(BaseModel):
    caps: Caps = Field(default_factory=Caps)
    poll_seconds: PollSeconds = Field(default_factory=PollSeconds)
    alert_dedupe_hours: int = 6
    sites: Sites = Field(default_factory=Sites)
    state_db: str = "state.db"
    log_file: str = "movementtix.log"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    seatgeek_client_id: str = ""

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Config":
        load_dotenv()
        data: dict = {}
        p = Path(path)
        if p.exists():
            data = yaml.safe_load(p.read_text()) or {}
        cfg = cls(**data)
        cfg.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        cfg.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        cfg.seatgeek_client_id = os.getenv("SEATGEEK_CLIENT_ID", "")
        return cfg


# Best-effort event IDs per site. Some are confirmed via search; others must be
# filled in after a one-time manual lookup. A scraper that has no ID for the
# requested pass type will return None gracefully.
EVENT_IDS: dict[str, dict[PassType, str]] = {
    "axs": {
        PassType.THREE_DAY: "1183251",
        PassType.SATURDAY: "1285169",
    },
    "vividseats": {
        PassType.THREE_DAY: "6136478",
        # Saturday production ID — fill in after one-time lookup on vividseats.com
        PassType.SATURDAY: "",
    },
    "tixel": {PassType.THREE_DAY: "", PassType.SATURDAY: ""},
    "stubhub": {PassType.THREE_DAY: "", PassType.SATURDAY: ""},
    "viagogo": {PassType.THREE_DAY: "", PassType.SATURDAY: ""},
    # SeatGeek scraper resolves IDs at runtime via the events search API.
    "seatgeek": {PassType.THREE_DAY: "", PassType.SATURDAY: ""},
}
