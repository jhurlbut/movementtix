from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod

import httpx

from ..config import Config
from ..models import Listing, PassType

log = logging.getLogger(__name__)

UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


def random_ua() -> str:
    return random.choice(UA_POOL)


class Scraper(ABC):
    name: str = "base"

    def __init__(self, config: Config):
        self.config = config

    @abstractmethod
    def fetch_lowest(self, pass_type: PassType) -> Listing | None:
        """Return the cheapest listing for this pass type, or None.

        Implementations should swallow recoverable network errors and log
        them; raise only on programmer error.
        """

    def _client(self, **kwargs) -> httpx.Client:
        headers = {
            "User-Agent": random_ua(),
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        headers.update(kwargs.pop("headers", {}))
        return httpx.Client(timeout=20, headers=headers, follow_redirects=True, **kwargs)
