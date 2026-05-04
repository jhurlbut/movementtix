from __future__ import annotations

import argparse
import logging
import sys

import httpx

from .config import Config
from .models import AlertReason, Listing

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{token}"

    def _post(self, method: str, payload: dict) -> dict:
        with httpx.Client(timeout=15) as client:
            r = client.post(f"{self.base}/{method}", json=payload)
            r.raise_for_status()
            return r.json()

    def send_message(self, text: str) -> None:
        if not self.token or not self.chat_id:
            log.warning("telegram creds missing; skipping send: %s", text[:80])
            return
        self._post(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
        )

    def get_updates(self) -> dict:
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{self.base}/getUpdates")
            r.raise_for_status()
            return r.json()


def format_alert(listing: Listing, reason: AlertReason, prior_min: float | None) -> str:
    reason_label = {
        AlertReason.UNDER_CAP: "Under cap",
        AlertReason.NEW_LOW: "New all-time low",
    }[reason]
    prior = f"prev low ${prior_min:.2f}" if prior_min is not None else "first sighting"
    qty = listing.quantity
    group_total_line = ""
    if qty > 1:
        group = listing.total_price * qty
        group_total_line = f"Group total ({qty}× tix): *${group:,.2f}*\n"
    return (
        f"*Movement 2026 — {reason_label}*\n"
        f"{listing.pass_type.display}\n"
        f"Site: `{listing.site}`\n"
        f"Price/ticket: *${listing.total_price:.2f}* "
        f"(${listing.base_price:.2f} + ${listing.fees:.2f} fees)\n"
        + group_total_line
        + f"Qty: {qty}"
        + (f"  Sec: {listing.section}" if listing.section else "")
        + f"\n[Open listing]({listing.url})\n"
        f"_{prior}_"
    )


def cli() -> None:
    parser = argparse.ArgumentParser(description="Telegram smoke test")
    parser.add_argument("--test", action="store_true", help="send a hello message")
    parser.add_argument("--get-chat-id", action="store_true",
                        help="print getUpdates so you can find your chat_id")
    args = parser.parse_args()

    cfg = Config.load()
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)

    if args.get_chat_id:
        if not cfg.telegram_bot_token:
            sys.exit("TELEGRAM_BOT_TOKEN not set in .env")
        import json as _json
        print(_json.dumps(tg.get_updates(), indent=2))
        return

    if args.test:
        tg.send_message("movementtix tracker is alive ✅")
        print("sent")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli()
