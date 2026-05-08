from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import time
from .config import Config
from .models import PassType
from .notify import Telegram, format_alert, source_tag
from .pricing import should_alert
from .reddit import poll_and_alert as reddit_poll_and_alert
from .scrapers import ALL_SCRAPERS

log = logging.getLogger("movementtix")


def _setup_logging(log_file: str, verbose: bool) -> None:
    """Single StreamHandler to stdout. When run.sh nohups the process,
    stdout is appended to movementtix.log; that file IS the log. Adding
    a FileHandler here would double every line (both nohup and the
    handler write to the same file)."""
    fmt = "%(asctime)s %(levelname)s %(name)s | %(message)s"
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=fmt,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def _build_scrapers(cfg: Config, only: str | None) -> list:
    enabled = cfg.sites.enabled()
    out = []
    for name in enabled:
        if only and only != name:
            continue
        cls = ALL_SCRAPERS.get(name)
        if not cls:
            continue
        out.append(cls(cfg))
    return out


def _fanout(tg: Telegram, state, text: str, dry_run: bool, label: str,
            subs: list[int] | None = None) -> int:
    """Broadcast a Markdown message to a list of subscribers.

    If ``subs`` is None, falls back to every active subscriber (used for
    Reddit alerts and the startup summary, which aren't tied to a
    pass_type/tier). Ticket alerts pass an explicit list filtered by
    each subscriber's watchlist.

    Auto-deactivates chats that 400/403 (blocked / not found). Returns
    the number of successful deliveries. In dry_run mode, just logs."""
    if subs is None:
        subs = state.active_subscribers()
    if not subs:
        log.info("  no subscribers to send %s to", label)
        return 0
    if dry_run:
        log.info("  [dry-run] would fanout %s to %d sub(s):\n%s",
                 label, len(subs), text)
        return 0
    sent, dead = tg.fanout(text, subs)
    for cid in dead:
        state.remove_subscriber(cid)
    if dead:
        log.info("  deactivated %d unreachable subscriber(s): %s", len(dead), dead)
    log.info("  fanout %s sent to %d/%d", label, sent, len(subs))
    return sent


def run_once(cfg: Config, dry_run: bool, only_site: str | None,
             only_pass: str | None, drain_commands: bool = True) -> list:
    """Run one full sweep. Returns the list of Listings collected this cycle.

    When ``drain_commands`` is False, skips the inline Telegram getUpdates
    poll. Set this in long-running daemon mode where a separate listener
    process owns the offset (see ``movementtix.listener``); two readers
    of getUpdates with independent offsets would silently drop messages.
    The CI ``--once`` path keeps drain_commands=True since it has no
    listener running alongside.
    """
    from .state import State
    from .commands import process_pending as process_commands

    state = State(cfg.state_db)
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)

    if drain_commands:
        try:
            n = process_commands(tg, state)
            if n:
                log.info("processed %d command(s); subscribers=%d",
                         n, state.subscriber_count())
        except Exception as e:
            log.exception("command poll failed: %s", e)

    pass_types = (
        [PassType(only_pass)] if only_pass else list(PassType)
    )
    cycle: list = []

    # Phase 1: scrape every (site, pass_type) and record the listings.
    # No alerting in this phase — we only fire one alert per pass_type
    # below, against the cycle's GLOBAL cheapest, not per-site.
    for scraper in _build_scrapers(cfg, only_site):
        for pt in pass_types:
            log.info("→ %s / %s", scraper.name, pt.value)
            try:
                listing = scraper.fetch_lowest(pt)
            except Exception as e:
                log.exception("scraper %s crashed: %s", scraper.name, e)
                continue
            if not listing:
                continue
            cycle.append(listing)
            state.record(listing)
            log.info(
                "  found ${:.2f} (qty {}) on {}".format(
                    listing.total_price, listing.quantity, scraper.name
                )
            )

    # Phase 2: cross-platform alert decision. For each pass_type, find
    # the cheapest listing across every site this cycle and compare
    # against the global cheapest from the previous cycle. A site
    # whose own price dropped but is still pricier than another site
    # will NOT fire — only the global cheapest does.
    for pt in pass_types:
        pt_listings = [l for l in cycle if l.pass_type is pt]
        if not pt_listings:
            continue
        cheapest = min(pt_listings, key=lambda x: x.total_price)
        prior_global = state.prior_global_min(pt)
        log.info(
            "  cycle min %s: $%.2f via %s (prev global %s)",
            pt.value,
            cheapest.total_price,
            cheapest.site,
            f"${prior_global:.2f}" if prior_global is not None else "n/a",
        )
        reason = should_alert(cheapest, prior_global, cfg.caps)
        # Always update the global min for next cycle, regardless of
        # whether we alerted — the next cycle's comparison is against
        # this cycle's number.
        state.set_global_min(pt, cheapest.total_price)
        if not reason:
            continue
        if state.recently_alerted(cheapest, cfg.alert_dedupe_hours):
            log.info("  alert suppressed (dedupe)")
            continue
        text = format_alert(cheapest, reason, prior_global)
        interested = state.watching_subscribers(pt, cheapest.tier)
        sent = _fanout(
            tg, state, text, dry_run,
            label=f"alert ({reason.value} {cheapest.site}/{pt.value}/{cheapest.tier.value})",
            subs=interested,
        )
        if not dry_run and sent >= 0:
            state.mark_alerted(cheapest)

    # Reddit feed runs once per cycle (no per-pass-type loop)
    if not only_site or only_site == "reddit":
        try:
            n = reddit_poll_and_alert(cfg, state, dry_run, tg)
            if n:
                log.info("reddit: %d alert(s) %s", n, "would send" if dry_run else "sent")
        except Exception as e:
            log.exception("reddit poll failed: %s", e)

    state.close()
    return cycle


_running = True


def _stop(*_):
    global _running
    _running = False
    log.info("shutdown signal received")


def _format_startup_summary(cfg: Config, cycle: list) -> str:
    """Group the cycle's cheapest by pass type and render a short Telegram
    snapshot."""
    from .models import PassType as _PT
    by_pt: dict = {pt: [] for pt in _PT}
    for listing in cycle:
        by_pt.setdefault(listing.pass_type, []).append(listing)

    lines = ["*movementtix tracker started*",
             "_Cheapest available across all sites:_", ""]
    for pt in _PT:
        items = sorted(by_pt.get(pt, []), key=lambda x: x.total_price)
        cap = cfg.caps.for_pass(pt)
        label = pt.display.split(" Pass")[0]
        if not items:
            lines.append(f"_{label}_  no listings found  (cap ${cap:.0f})")
            continue
        it = items[0]  # global cheapest for this pass_type
        qty_note = f" ×{it.quantity}" if it.quantity > 1 else ""
        site = f"[{it.site}]({it.url})" if it.url else f"`{it.site}`"
        lines.append(
            f"_{label}_  ${it.total_price:.2f}/tix{qty_note} on {site}"
            f"  (cap ${cap:.0f})"
        )
    lines.append("")
    lines.append(
        f"Polling every {cfg.poll_seconds.min // 60}–{cfg.poll_seconds.max // 60} min."
    )
    lines.append(source_tag())
    return "\n".join(lines)


def run_forever(cfg: Config, dry_run: bool, only_site: str | None,
                only_pass: str | None) -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    log.info(
        "starting loop: sites=%s caps=3day≤$%.0f sat≤$%.0f poll=%d-%ds",
        cfg.sites.enabled(),
        cfg.caps.three_day,
        cfg.caps.saturday,
        cfg.poll_seconds.min,
        cfg.poll_seconds.max,
    )
    from .commands import POLL_REQUEST_KEY, _status_text
    from .state import State

    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)
    # Long-lived State just for kv polling (run_once owns its own State
    # for the actual scrape work). 1 Hz checks during sleep — the open
    # connection avoids re-opening sqlite every second.
    poll_state = State(cfg.state_db)
    first_cycle = True
    while _running:
        # Capture whether this cycle was triggered by a /poll request,
        # so after the cycle we can DM the requester regardless of
        # whether any individual listing crossed an alert threshold.
        poll_req_before = poll_state.kv_get(POLL_REQUEST_KEY)

        cycle = run_once(cfg, dry_run, only_site, only_pass, drain_commands=False)

        if first_cycle:
            first_cycle = False
            summary = _format_startup_summary(cfg, cycle)
            if dry_run:
                log.info("[dry-run] startup summary:\n%s", summary)
            else:
                try:
                    tg.send_message(summary)
                    log.info("startup summary sent (%d listings)", len(cycle))
                except Exception as e:
                    log.exception("startup summary send failed: %s", e)

        # /poll result delivery. We re-read the key in case the request
        # arrived during this cycle (still counts — user wants the
        # freshest possible snapshot).
        poll_req = poll_req_before or poll_state.kv_get(POLL_REQUEST_KEY)
        if poll_req:
            try:
                requester_chat = int(poll_req.split(":")[0])
                text = "🔄 *Manual poll complete*\n\n" + _status_text(poll_state)
                if not dry_run:
                    tg.send_to(requester_chat, text)
                log.info("poll result delivered to chat=%s", requester_chat)
            except Exception as e:
                log.exception("poll result send failed: %s", e)
            finally:
                poll_state.kv_delete(POLL_REQUEST_KEY)

        if not _running:
            break
        delay = random.uniform(cfg.poll_seconds.min, cfg.poll_seconds.max)
        log.info("sleeping %.0fs", delay)
        for _ in range(int(delay)):
            if not _running:
                break
            # Manual /poll request — break sleep early to start the
            # next cycle. The result DM happens at the END of that
            # cycle (see poll_req block above).
            if poll_state.kv_get(POLL_REQUEST_KEY):
                log.info("/poll requested; breaking sleep early")
                break
            time.sleep(1)
    poll_state.close()


def cli() -> None:
    p = argparse.ArgumentParser(prog="movementtix")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--once", action="store_true", help="single pass then exit")
    p.add_argument("--dry-run", action="store_true", help="don't send alerts")
    p.add_argument("--site", help="restrict to one scraper")
    p.add_argument("--pass-type", choices=[p.value for p in PassType])
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    cfg = Config.load(args.config)
    _setup_logging(cfg.log_file, args.verbose)

    if args.once:
        run_once(cfg, args.dry_run, args.site, args.pass_type)
    else:
        run_forever(cfg, args.dry_run, args.site, args.pass_type)


if __name__ == "__main__":
    cli()
