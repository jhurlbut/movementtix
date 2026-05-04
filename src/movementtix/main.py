from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import time
from .config import Config
from .models import PassType
from .notify import Telegram, format_alert
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


def run_once(cfg: Config, dry_run: bool, only_site: str | None,
             only_pass: str | None) -> list:
    """Run one full sweep. Returns the list of Listings collected this cycle."""
    from .state import State
    state = State(cfg.state_db)
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)
    pass_types = (
        [PassType(only_pass)] if only_pass else [PassType.THREE_DAY, PassType.SATURDAY]
    )
    cycle: list = []

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
            prior_min = state.prior_min(scraper.name, pt)
            state.record(listing)
            log.info(
                "  found ${:.2f} (qty {}) — prev min {}".format(
                    listing.total_price,
                    listing.quantity,
                    f"${prior_min:.2f}" if prior_min is not None else "n/a",
                )
            )
            reason = should_alert(listing, prior_min, cfg.caps)
            if not reason:
                continue
            if state.recently_alerted(listing, cfg.alert_dedupe_hours):
                log.info("  alert suppressed (dedupe)")
                continue
            text = format_alert(listing, reason, prior_min)
            if dry_run:
                log.info("  [dry-run] would send:\n%s", text)
            else:
                try:
                    tg.send_message(text)
                    state.mark_alerted(listing)
                    log.info("  alert sent (%s)", reason.value)
                except Exception as e:
                    log.exception("telegram send failed: %s", e)

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
    by_pt: dict = {_PT.THREE_DAY: [], _PT.SATURDAY: []}
    for listing in cycle:
        by_pt.setdefault(listing.pass_type, []).append(listing)

    lines = ["*movementtix tracker started*", ""]
    for pt in (_PT.THREE_DAY, _PT.SATURDAY):
        items = sorted(by_pt.get(pt, []), key=lambda x: x.total_price)
        cap = cfg.caps.for_pass(pt)
        lines.append(f"_{pt.display}_  (cap ${cap:.0f})")
        if not items:
            lines.append("  no listings found")
        else:
            for it in items[:6]:
                section = f" {it.section}" if it.section else ""
                lines.append(
                    f"  `{it.site}` ${it.total_price:.2f}{section}"
                )
        lines.append("")
    lines.append(
        f"Polling every {cfg.poll_seconds.min // 60}–{cfg.poll_seconds.max // 60} min."
    )
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
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)
    first_cycle = True
    while _running:
        cycle = run_once(cfg, dry_run, only_site, only_pass)
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
        if not _running:
            break
        delay = random.uniform(cfg.poll_seconds.min, cfg.poll_seconds.max)
        log.info("sleeping %.0fs", delay)
        for _ in range(int(delay)):
            if not _running:
                break
            time.sleep(1)


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
