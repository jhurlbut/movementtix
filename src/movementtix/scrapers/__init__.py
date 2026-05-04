from .axs import AxsScraper
from .base import Scraper
from .seatgeek import SeatGeekScraper
from .stubhub import StubHubScraper
from .tixel import TixelScraper
from .viagogo import ViagogoScraper
from .vividseats import VividSeatsScraper

ALL_SCRAPERS: dict[str, type[Scraper]] = {
    "tixel": TixelScraper,
    "axs": AxsScraper,
    "stubhub": StubHubScraper,
    "viagogo": ViagogoScraper,
    "vividseats": VividSeatsScraper,
    "seatgeek": SeatGeekScraper,
}

__all__ = ["ALL_SCRAPERS", "Scraper"]
