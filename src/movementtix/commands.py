"""Process incoming Telegram commands (/start, /stop, /help, /status,
/watch, /unwatch, /watching).

Called once per poll cycle. Uses long-polling getUpdates with an offset
stored in state.kv so we don't re-process the same command across runs.
"""
from __future__ import annotations

import logging
from datetime import datetime

from .models import PassType, Tier
from .notify import Telegram
from .state import State

log = logging.getLogger(__name__)

OFFSET_KEY = "tg_updates_offset"
POLL_REQUEST_KEY = "poll_request"


WELCOME = (
    "👋 Hi! You're subscribed to *movementtix* — Movement Music Festival "
    "2026 ticket alerts (Detroit, May 23–25).\n\n"
    "I'll DM you when a 3-day, Saturday, Sunday, or Monday pass drops "
    "below your alert threshold or sets a new all-time low.\n\n"
    "*Subscription*\n"
    "/start — subscribe (you just did this)\n"
    "/stop — unsubscribe\n"
    "/help — show this message\n\n"
    "*Watchlist*\n"
    "/watching — show what you're tracking\n"
    "/watch `<pass> [tier]` — start tracking a combo\n"
    "/unwatch `<pass> [tier]` — stop tracking a combo\n"
    "_pass_: `3day` `saturday` `sunday` `monday`\n"
    "_tier_: `ga` `vip` (omit = both)\n"
    "Examples: `/watch saturday vip` · `/unwatch monday ga`\n"
    "By default you watch every combo until you /unwatch any.\n\n"
    "*Prices*\n"
    "/status — current cheapest known prices\n"
    "/poll — trigger an immediate scrape and DM the result"
)

ALREADY = "You're already subscribed. /stop to leave, /status for current prices."

GOODBYE = "✓ Unsubscribed. /start anytime to resubscribe."

NOT_SUBBED = "You weren't subscribed. /start to subscribe."

UNKNOWN = (
    "Commands I understand: /start, /stop, /help, /status, /poll, "
    "/watch, /unwatch, /watching."
)

POLL_ACK = (
    "🔄 *Manual poll requested.*\n"
    "Breaking the scraper out of sleep — I'll DM the result when the "
    "cycle finishes (typically <2 min)."
)
POLL_PENDING = (
    "🔄 A manual poll is already in flight — sit tight, the result is "
    "coming."
)

WATCH_USAGE = (
    "Usage: `/watch <pass> [tier]`\n"
    "_pass_: `3day` `saturday` `sunday` `monday`\n"
    "_tier_: `ga` `vip` (omit = both)\n"
    "Examples: `/watch saturday vip` · `/watch 3day` (both tiers)"
)

UNWATCH_USAGE = (
    "Usage: `/unwatch <pass> [tier]`\n"
    "_pass_: `3day` `saturday` `sunday` `monday`\n"
    "_tier_: `ga` `vip` (omit = both)"
)

ALL_TIERS = (Tier.GA, Tier.VIP)


def _seed_default_watchlist(state: State, chat_id: int) -> None:
    """On first /start, give the subscriber every combo so they receive
    everything until they explicitly /unwatch. Idempotent — safe to call
    on re-/start."""
    for pt in PassType:
        for tier in ALL_TIERS:
            state.add_watch(chat_id, pt, tier)


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
    parts = text.split()
    cmd = parts[0].lower()
    # Strip "@botname" suffix if present (Telegram convention in groups)
    cmd = cmd.split("@", 1)[0]
    args = parts[1:]

    if cmd == "/start":
        added = state.add_subscriber(
            chat_id,
            sender.get("username"),
            sender.get("first_name"),
        )
        if added:
            _seed_default_watchlist(state, chat_id)
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
    elif cmd == "/watching":
        tg.send_to(chat_id, _watching_text(state, chat_id))
    elif cmd == "/poll":
        existing = state.kv_get(POLL_REQUEST_KEY)
        if existing:
            tg.send_to(chat_id, POLL_PENDING)
        else:
            # Value format: "<chat_id>:<unix_ts>" — chat_id is the
            # destination for the result DM, ts is informational.
            import time as _t
            state.kv_set(POLL_REQUEST_KEY, f"{chat_id}:{int(_t.time())}")
            log.info("poll requested by chat=%s", chat_id)
            tg.send_to(chat_id, POLL_ACK)
    elif cmd == "/watch":
        tg.send_to(chat_id, _handle_watch(state, chat_id, args, add=True))
    elif cmd == "/unwatch":
        tg.send_to(chat_id, _handle_watch(state, chat_id, args, add=False))
    else:
        tg.send_to(chat_id, UNKNOWN)


def _handle_watch(state: State, chat_id: int, args: list[str], add: bool) -> str:
    usage = WATCH_USAGE if add else UNWATCH_USAGE
    if not args:
        return usage
    pt = PassType.parse(args[0])
    if pt is None:
        return f"Unknown pass type `{args[0]}`.\n\n{usage}"
    if len(args) >= 2:
        tier = Tier.parse(args[1])
        if tier is None:
            return f"Unknown tier `{args[1]}`.\n\n{usage}"
        tiers = (tier,)
    else:
        tiers = ALL_TIERS

    # Empty watchlist semantically means "watching everything" (the
    # default for legacy subscribers and freshly-/start'd users whose
    # default seed hasn't run). When the user issues /unwatch in that
    # state, materialize the full default first so the remove actually
    # narrows the list. Without this, /unwatch saturday vip on a
    # subscriber with no rows would be a no-op and they'd keep getting
    # Saturday VIP alerts.
    if not add and not state.has_any_watch(chat_id):
        _seed_default_watchlist(state, chat_id)

    changed = []
    unchanged = []
    for t in tiers:
        if add:
            ok = state.add_watch(chat_id, pt, t)
        else:
            ok = state.remove_watch(chat_id, pt, t)
        (changed if ok else unchanged).append(t)

    verb = "added" if add else "removed"
    label = pt.display.split(" Pass")[0]
    if changed:
        tier_list = "/".join(t.display for t in changed)
        msg = f"✓ {verb}: *{label}* ({tier_list})"
        if unchanged:
            tier_list2 = "/".join(t.display for t in unchanged)
            msg += f"\n(already {'on' if add else 'off'}: {tier_list2})"
    else:
        tier_list = "/".join(t.display for t in tiers)
        msg = (
            f"No change — *{label}* ({tier_list}) "
            f"already {'on' if add else 'off'} your list."
        )
    return msg + "\n\n" + _watching_text(state, chat_id)


def _watching_text(state: State, chat_id: int) -> str:
    rows = state.list_watch(chat_id)
    if not rows:
        # Empty watchlist = "watching everything" by default — see
        # State.watching_subscribers, which sends every alert to subs
        # who have not customized their list. /watch and /unwatch make
        # the list explicit.
        return (
            "*Watchlist:* (default — watching all 8 combos)\n"
            "Use `/unwatch <pass> [tier]` to drop one, or `/watch <pass>` "
            "after that to add it back. Combos: "
            "_3day_ · _saturday_ · _sunday_ · _monday_ × _ga_ / _vip_."
        )
    by_pt: dict[PassType, list[Tier]] = {}
    for pt, tier in rows:
        by_pt.setdefault(pt, []).append(tier)
    lines = ["*Watchlist*"]
    for pt in PassType:
        tiers = sorted(by_pt.get(pt, []), key=lambda t: t.value)
        if not tiers:
            continue
        label = pt.display.split(" Pass")[0]
        tier_list = "/".join(t.display for t in tiers)
        lines.append(f"  • *{label}* — {tier_list}")
    return "\n".join(lines)


def _status_text(state: State) -> str:
    """Cheapest currently-available ticket per pass_type, across all
    sites. We deliberately do NOT show per-site rows — alerts and
    display alike track only the global cheapest, since a site dropping
    its price by $5 while still being more expensive than another site
    isn't actionable."""
    lines = ["*Cheapest available across all sites*\n"]
    for pt in PassType:
        # Each cycle writes one row per (site, pass_type). Get the most
        # recent row per site via MAX(fetched_at) (SQLite carries the
        # other columns from that row), then the cheapest among those.
        row = state._conn.execute(
            "SELECT site, total_price, quantity, MAX(fetched_at) as f, url, tier "
            "FROM listings WHERE pass_type=? GROUP BY site ORDER BY total_price LIMIT 1",
            (pt.value,),
        ).fetchone()
        label = pt.display.split(" Pass")[0]
        if not row:
            lines.append(f"_{label}_  no data yet")
            continue
        site, price, qty, ts, url, tier = row
        local = datetime.fromisoformat(ts).astimezone()
        qty_note = f" ×{qty}" if qty and qty > 1 else ""
        tier_note = f" {tier.upper()}" if tier and tier != "unknown" else ""
        link = f"[{site}]({url})" if url else f"`{site}`"
        lines.append(
            f"_{label}_  ${price:.2f}/tix{qty_note}{tier_note} on {link}  "
            f"_(seen {local.strftime('%H:%M %Z')})_"
        )
    lines.append(f"\nSubscribers: {state.subscriber_count()}")
    return "\n".join(lines)
