"""Standalone Telegram command listener.

Runs as its own process so /status, /start, /stop, /help reply instantly
regardless of what the scraper daemon is doing (or whether it's even
running). Uses long-poll getUpdates with a 25s server-side timeout, so
commands dispatch within ~2s of arrival.

Shares only state.db with the scraper. SQLite WAL handles cross-process
concurrent writes (listener: subscribers/kv vs scraper: listings/alerts).
"""
from __future__ import annotations

import logging
import signal
import sys
import time

import httpx

from .commands import OFFSET_KEY, _dispatch
from .config import Config
from .notify import Telegram
from .state import State

log = logging.getLogger("movementtix.listener")

# Telegram long-poll: server holds the request open until an update
# arrives or this many seconds pass. httpx client must allow longer.
LONG_POLL_SECONDS = 25
HTTP_TIMEOUT = 30

_running = True


def _stop(*_):
    global _running
    _running = False
    log.info("shutdown signal received")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def _poll_once(client: httpx.Client, token: str, offset: int) -> list:
    """One long-poll request. Returns the list of update dicts (possibly
    empty). Raises httpx.HTTPError on hard failure; ReadTimeout means
    "no updates" and is converted to []."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        r = client.get(
            url,
            params={"offset": offset, "timeout": LONG_POLL_SECONDS},
        )
        r.raise_for_status()
    except httpx.ReadTimeout:
        return []
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"telegram getUpdates not ok: {body}")
    return body.get("result", []) or []


def run_loop(cfg: Config) -> None:
    if not cfg.telegram_bot_token:
        log.error("TELEGRAM_BOT_TOKEN not set; listener cannot start")
        return

    state = State(cfg.state_db)
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)

    offset_str = state.kv_get(OFFSET_KEY)
    offset = int(offset_str) + 1 if offset_str else 0

    backoff = 1.0
    log.info("listener up; long-poll=%ds offset=%s", LONG_POLL_SECONDS, offset)

    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        while _running:
            try:
                updates = _poll_once(client, cfg.telegram_bot_token, offset)
            except httpx.HTTPError as e:
                log.warning("getUpdates http error: %s; backoff %.1fs", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            except Exception as e:
                log.exception("getUpdates failed: %s; backoff %.1fs", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            backoff = 1.0
            if not updates:
                continue

            for upd in updates:
                update_id = upd.get("update_id")
                if update_id is not None:
                    offset = update_id + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                chat = msg.get("chat") or {}
                chat_id = chat.get("id")
                if not chat_id:
                    continue
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                sender = msg.get("from") or {}
                try:
                    _dispatch(tg, state, chat_id, text, sender)
                except Exception as e:
                    log.exception("dispatch failed for chat=%s: %s", chat_id, e)

            try:
                state.kv_set(OFFSET_KEY, str(offset - 1))
            except Exception as e:
                log.exception("kv_set offset failed: %s", e)

    state.close()
    log.info("listener exited cleanly")


def cli() -> None:
    _setup_logging()
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    cfg = Config.load()
    while _running:
        try:
            run_loop(cfg)
            break
        except Exception as e:
            log.exception("listener crashed: %s; restarting in 5s", e)
            time.sleep(5)


if __name__ == "__main__":
    cli()
