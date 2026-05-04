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
    eventim: bool = True
    axs: bool = True
    stubhub: bool = True
    viagogo: bool = True
    vividseats: bool = True
    seatgeek: bool = True

    def enabled(self) -> list[str]:
        return [name for name, on in self.model_dump().items() if on]


class BrowserConfig(BaseModel):
    """Controls how Playwright launches a browser for AXS / Viagogo /
    other heavily-protected sites.

    Three modes (auto-detected in this order of preference):
      1. `cdp_url` set → connect to a running Chrome via DevTools Protocol.
         Recommended: start Chrome with
           chrome --remote-debugging-port=9222 --user-data-dir=...
         and put `cdp_url: http://localhost:9222` here.
      2. `user_data_dir` set → launch a persistent Playwright Chromium
         using that directory (cookies + fingerprint persist across runs).
      3. Neither set → ephemeral headless Chromium (default; lowest
         success rate against Datadome).
    """

    cdp_url: str = ""
    user_data_dir: str = ""
    headless: bool = True
    channel: str = ""  # "chrome" | "msedge" | "" for bundled chromium


class RedditFeed(BaseModel):
    enabled: bool = True
    subreddit: str = "MovementDEMF"
    fetch_limit: int = 50
    keywords: list[str] = Field(
        default_factory=lambda: [
            "selling", "sell my", "for sale", "wts", "resale", "resell", "re-sell",
            "iso", "in search of", "looking for", "wristband", "3-day", "3 day",
            "saturday pass", "sunday pass", "monday pass",
            "after party", "after-party", "afterparty", "afters",
        ]
    )


class Config(BaseModel):
    caps: Caps = Field(default_factory=Caps)
    poll_seconds: PollSeconds = Field(default_factory=PollSeconds)
    alert_dedupe_hours: int = 6
    sites: Sites = Field(default_factory=Sites)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    reddit: RedditFeed = Field(default_factory=RedditFeed)
    state_db: str = "state.db"
    log_file: str = "movementtix.log"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    seatgeek_client_id: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_username: str = ""

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
        cfg.reddit_client_id = os.getenv("REDDIT_CLIENT_ID", "")
        cfg.reddit_client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
        cfg.reddit_username = os.getenv("REDDIT_USERNAME", "")
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
        PassType.SATURDAY: "6482557",
    },
    "tixel": {PassType.THREE_DAY: "", PassType.SATURDAY: ""},
    "stubhub": {
        PassType.THREE_DAY: "159631798",
        PassType.SATURDAY: "159867649",
    },
    # Viagogo and StubHub share inventory and use the same numeric event ID.
    "viagogo": {
        PassType.THREE_DAY: "159631798",
        PassType.SATURDAY: "159867649",
    },
    # SeatGeek scraper resolves IDs at runtime via the events search API.
    "seatgeek": {PassType.THREE_DAY: "", PassType.SATURDAY: ""},
}
