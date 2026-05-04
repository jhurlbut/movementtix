"""Process incoming Telegram commands (/start, /stop, /help, /status).

Called once per poll cycle. Uses long-polling getUpdates with an offset
stored in state.kv so we don't re-process the same command across runs.
"""
from __future__ import annotations

import logging

from .notify import Telegram
from .state import State

log = logging.getLogger(__name__)

OFFSET_KEY = "tg_updates_offset"


WELCOME = (
    "👋 Hi! You're subscribed to *movementtix* — Movement Music Festival "
    "2026 ticket alerts (Detroit, May 23–25).\n\n"
    "I'll DM you when a 3-day or Saturday pass on Tixel / StubHub / "
    "Vivid Seats drops below your alert threshold or sets a new "
    "all-time low.\n\n"
    "*Commands*\n"
    "/start — subscribe (you just did this)\n"
    "/stop — unsubscribe\n"
    "/status — current cheapest known prices\n"
    "/help — show this message"
)

ALREADY = "You're already subscribed. /stop to leave, /status for current prices."

GOODBYE = "✓ Unsubscribed. /start anytime to resubscribe."

NOT_SUBBED = "You weren't subscribed. /start to subscribe."

UNKNOWN = (
    "I only understand /start, /stop, /status, /help.\n"
    "I'm a personal alert bot — I can't reply to free-text messages."
)


def process_pending(tg: Telegram, state: State) -> int:
    """Pull queued commands via getUpdates, dispatch handlers, advance the
    offset. Returns number of commands processed."""
    if not tg.token:
        return 0
    offset_str = state.kv_get(OFFSET_KEY)
    offset = int(offset_str) + 1 if offset_str else 0

    try:
        data = tg.api_get("getUpdates", params={"offset": offset, "timeout": 0})
    except Exception as e:
        log.warning("getUpdates failed: %s", e)
        return 0

    updates = data.get("result", []) or []
    handled = 0
    last_id = None
    for upd in updates:
        last_id = upd.get("update_id")
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
        _dispatch(tg, state, chat_id, text, sender)
        handled += 1

    if last_id is not None:
        state.kv_set(OFFSET_KEY, str(last_id))
    return handled


def _dispatch(tg: Telegram, state: State, chat_id: int, text: str,
              sender: dict) -> None:
    cmd = text.split(maxsplit=1)[0].lower()
    # Strip "@botname" suffix if present (Telegram convention in groups)
    cmd = cmd.split("@", 1)[0]

    if cmd == "/start":
        added = state.add_subscriber(
            chat_id,
            sender.get("username"),
            sender.get("first_name"),
        )
        reply = WELCOME if added else ALREADY
        log.info("subscribe: chat=%s user=%s new=%s",
                 chat_id, sender.get("username") or sender.get("first_name"), added)
        tg.send_to(chat_id, reply)
    elif cmd in ("/stop", "/unsubscribe"):
        removed = state.remove_subscriber(chat_id)
        log.info("unsubscribe: chat=%s removed=%s", chat_id, removed)
        tg.send_to(chat_id, GOODBYE if removed else NOT_SUBBED)
    elif cmd == "/help":
        tg.send_to(chat_id, WELCOME)
    elif cmd == "/status":
        tg.send_to(chat_id, _status_text(state))
    else:
        tg.send_to(chat_id, UNKNOWN)


def _status_text(state: State) -> str:
    from .models import PassType
    lines = ["*Current cheapest known*\n"]
    for pt in (PassType.THREE_DAY, PassType.SATURDAY):
        lines.append(f"_{pt.display}_")
        # Fetch min per site for this pass type
        rows = state._conn.execute(
            "SELECT site, MIN(total_price), MAX(quantity), MAX(fetched_at) "
            "FROM listings WHERE pass_type=? GROUP BY site ORDER BY 2",
            (pt.value,),
        ).fetchall()
        if not rows:
            lines.append("  no data yet")
        else:
            for site, price, qty, ts in rows:
                lines.append(f"  `{site}` ${price:.2f}/tix  (last seen {ts[:16]} UTC)")
        lines.append("")
    lines.append(f"Subscribers: {state.subscriber_count()}")
    return "\n".join(lines)
