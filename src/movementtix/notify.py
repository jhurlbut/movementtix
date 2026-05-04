from __future__ import annotations

import argparse
import logging
import os
import socket
import sys

import httpx

from .config import Config
from .models import AlertReason, Listing

log = logging.getLogger(__name__)


def source_tag() -> str:
    """Identify which deployment sent a Telegram message. Auto-detects:
      - GitHub Actions: links to the actual run.
      - Otherwise: 'local-daemon (<hostname>)'.
    """
    if os.getenv("GITHUB_ACTIONS") == "true":
        repo = os.getenv("GITHUB_REPOSITORY", "")
        run_id = os.getenv("GITHUB_RUN_ID", "")
        server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
        if repo and run_id:
            return f"_via [gh-actions #{run_id}]({server}/{repo}/actions/runs/{run_id})_"
        return "_via gh-actions_"
    return f"_via local-daemon ({socket.gethostname()})_"


class Telegram:
    def __init__(self, token: str, chat_id: str = ""):
        self.token = token
        # chat_id is the legacy single-recipient (kept for --test smoke
        # checks). The fanout path uses the subscribers table instead.
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{token}"

    def _post(self, method: str, payload: dict) -> dict:
        with httpx.Client(timeout=15) as client:
            r = client.post(f"{self.base}/{method}", json=payload)
            r.raise_for_status()
            return r.json()

    def api_get(self, method: str, params: dict | None = None) -> dict:
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{self.base}/{method}", params=params or {})
            r.raise_for_status()
            return r.json()

    def send_to(self, chat_id: int | str, text: str) -> bool:
        """Send a single Markdown message to one chat. Returns True on
        success, False if the bot can't deliver (blocked, deactivated,
        or chat not found) so the caller can purge the subscriber."""
        if not self.token:
            log.warning("telegram token missing; skipping send")
            return False
        try:
            self._post(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False,
                },
            )
            return True
        except httpx.HTTPStatusError as e:
            body = ""
            try:
                body = e.response.text[:300]
            except Exception:
                pass
            # 403 = bot blocked, 400 chat not found, etc. → caller should
            # deactivate that subscriber rather than retry.
            if e.response.status_code in (400, 403):
                log.info("telegram drop chat=%s: %s", chat_id, body)
                return False
            log.warning("telegram send to %s failed: %s %s",
                        chat_id, e.response.status_code, body)
            return False
        except httpx.HTTPError as e:
            log.warning("telegram send to %s network err: %s", chat_id, e)
            return False

    def fanout(self, text: str, chat_ids: list[int]) -> tuple[int, list[int]]:
        """Send `text` to every chat_id. Returns (sent_count, dead_ids)
        where dead_ids are recipients to deactivate."""
        sent, dead = 0, []
        for cid in chat_ids:
            ok = self.send_to(cid, text)
            if ok:
                sent += 1
            else:
                # Only deactivate on permanent failures we just logged
                # at INFO level — heuristic: 400/403 give back False.
                dead.append(cid)
        return sent, dead

    # Backwards-compat for the legacy single-recipient send (used by
    # `python -m movementtix.notify --test`).
    def send_message(self, text: str) -> None:
        if not self.chat_id:
            log.warning("legacy chat_id missing; skipping send")
            return
        self.send_to(self.chat_id, text)

    def get_updates(self) -> dict:
        return self.api_get("getUpdates")


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
        f"_{prior}_\n"
        f"{source_tag()}"
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
