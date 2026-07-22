"""Odyssey IMAX 70mm seat watcher for AMC Metreon 16.

Python port of odyssey_watch.js (which was written for a cloud sandbox with
its own proxy + browser paths). Checks AMC Metreon for "The Odyssey" in IMAX
70mm and reports NIGHT shows (>= 5:00pm) that have two AVAILABLE seats
together in row C or deeper (not the front rows; configurable) and not
wheelchair/companion.

AMC sits behind a Queue-it waiting room + Cloudflare WAF that 403s the JS
bundles, but the page ships its showtime + seat-map data server-rendered in
the HTML (Next.js RSC), so we parse that directly out of the
self.__next_f.push payloads.

Usage:
    python -m movementtix.odyssey            # one scan, print + alert
    python -m movementtix.odyssey --loop 900 # scan every 15 min
    python -m movementtix.odyssey --no-telegram

Alerts fan out to the movementtix subscriber list and dedupe through the
state.db kv table, so a pair that has already been announced is only
re-announced when the set of available pairs for that show changes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

from playwright.async_api import async_playwright

from .config import Config
from .notify import Telegram, source_tag
from .state import State

log = logging.getLogger(__name__)

THEATER = "amc-metreon-16"
THEATER_PATH = "san-francisco/amc-metreon-16"
MOVIE = "the-odyssey"
FORMAT = "imax70mm"
NIGHT_MIN_HOUR = 17  # 5:00pm local and later
BOOK_URL = "https://www.amctheatres.com/showtimes/{id}/seats"

# Tunables (env overrides keep parity with the JS version)
START_DATE = os.getenv("WATCH_START", "2000-01-01")  # inclusive floor; default = no floor
TARGET_DOW = {int(n) for n in os.getenv("WATCH_DOW", "0,1,2,3,4,5,6").split(",")}  # Sun=0..Sat=6
MIN_ROW = os.getenv("WATCH_MIN_ROW", "C").upper()  # exclude rows nearer the screen than this
MAX_DATES = int(os.getenv("WATCH_MAX_DATES", "60"))  # hard cap on dates probed per scan
EMPTY_STOP = int(os.getenv("WATCH_EMPTY_STOP", "3"))  # stop after this many consecutive no-show dates

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36")

KV_ALERTED = "odyssey_alerted"   # {showtime_id: {"sig": ..., "date": ...}}
KV_HEALTH = "odyssey_health"     # "ok" | "broken"


def gen_dates():
    """Qualifying dates from max(today, START_DATE) onward, unbounded.
    Today is included so last-minute cancellations before tonight's shows
    are caught; already-started shows are filtered out at seat-check time.
    The scan probes these until EMPTY_STOP consecutive dates have no shows
    (i.e. past AMC's booking horizon), so newly added dates are picked up
    automatically on later scans."""
    floor = date.fromisoformat(START_DATE)
    cur = max(date.today(), floor)
    while True:
        # Python: Mon=0..Sun=6; config uses Sun=0..Sat=6
        if (cur.weekday() + 1) % 7 in TARGET_DOW:
            yield cur.isoformat()
        cur += timedelta(days=1)


def rsc_blob(html: str) -> str:
    """Join and unescape the Next.js RSC payload pushed via self.__next_f."""
    pieces = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html)
    s = "".join(pieces)
    try:
        return json.loads('"' + s + '"')
    except (json.JSONDecodeError, ValueError):
        s = s.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


_SHOWTIME_RE = re.compile(
    r'"showtimeId":(\d+),"policyCodes":\[[^\]]*\],"hasTrailers":\w+,'
    r'"status":"([^"]+)","showDateTimeUtc":"([^"]+)",'
    r'"display":\{"time":"([^"]+)","amPm":"([^"]+)"\}'
    r'[\s\S]*?"aria-describedby":"([^"]+)"'
)


def parse_showtimes(blob: str) -> list[dict]:
    out = []
    for m in _SHOWTIME_RE.finditer(blob):
        out.append({
            "id": m.group(1),
            "status": m.group(2),
            "utc": m.group(3),
            "time": m.group(4) + m.group(5),
            "am_pm": m.group(5),
            "hour12": int(m.group(4).split(":")[0]),
            "aria": m.group(6),
        })
    return out


def parse_show_utc(u: str) -> datetime:
    dt = datetime.fromisoformat(u.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_night(s: dict) -> bool:
    h = s["hour12"] % 12
    if s["am_pm"] == "pm":
        h += 12
    return h >= NIGHT_MIN_HOUR


def parse_seat_layout(blob: str) -> list[dict] | None:
    m = re.search(r'"seatingLayout":\{"columns":(\d+),"rows":(\d+),"seats":\[', blob)
    if not m:
        return None
    start = m.end() - 1
    depth = 0
    end = -1
    for k in range(start, len(blob)):
        c = blob[k]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = k + 1
                break
    if end < 0:
        return None
    try:
        return json.loads(blob[start:end])
    except json.JSONDecodeError:
        return None


def find_pairs(seats: list[dict]) -> list[dict]:
    """Adjacent available regular-seat pairs in row MIN_ROW or deeper,
    sorted best-first by centrality."""
    by_row: dict[str, list[dict]] = {}
    for s in seats:
        if not s.get("shouldDisplay"):
            continue
        m = re.match(r"^([A-Z]+)", s.get("name", ""))
        letter = m.group(1) if m else "?"
        by_row.setdefault(letter, []).append(s)

    pairs = []
    for letter, rs in by_row.items():
        if len(letter) > 1 or letter < MIN_ROW:  # exclude rows nearer the screen (A=front)
            continue
        rs.sort(key=lambda s: s["column"])
        nums = []
        for s in rs:
            try:
                nums.append(int(s["name"][len(letter):]))
            except ValueError:
                pass
        if not nums:
            continue
        center = (min(nums) + max(nums)) / 2
        for a, b in zip(rs, rs[1:]):
            if a["column"] + 1 != b["column"]:
                continue
            if (a.get("available") and b.get("available")
                    and a.get("type") == "CanReserve" and b.get("type") == "CanReserve"):
                try:
                    na = int(a["name"][len(letter):])
                    nb = int(b["name"][len(letter):])
                except ValueError:
                    continue
                dist = (abs(na - center) + abs(nb - center)) / 2
                pairs.append({"row": letter, "seats": [a["name"], b["name"]],
                              "centerDist": round(dist, 1)})
    pairs.sort(key=lambda p: p["centerDist"])
    return pairs


async def fetch_html(ctx, url: str, needle: str = "") -> dict:
    page = await ctx.new_page()
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        status = resp.status if resp else 0
        for _ in range(40):
            u = page.url
            if not re.search(r"queue|waitingroom", u, re.I) and ("/showtimes" in u or "/seats" in u):
                break
            await page.wait_for_timeout(3000)
        ended_in_queue = bool(re.search(r"queue|waitingroom", page.url, re.I))
        await page.wait_for_timeout(2500)
        html = await page.content()
        if needle and needle not in html:
            await page.wait_for_timeout(4000)
            html = await page.content()
        return {"html": html, "status": status, "endedInQueue": ended_in_queue}
    finally:
        await page.close()


async def health_check(ctx) -> dict:
    """Canary: hit the live showtimes page (redirects to "today", always packed
    with on-sale shows). If it does not come back with a healthy, parseable set
    of showtimes, the scraper is blocked or AMC's markup changed — i.e. broken,
    and a "NONE" result can no longer be trusted."""
    url = f"https://www.amctheatres.com/movie-theatres/{THEATER_PATH}/showtimes"
    try:
        c = await fetch_html(ctx, url)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"canary fetch failed: {e}", "canaryShowtimes": 0}
    blob = rsc_blob(c["html"])
    shows = len(parse_showtimes(blob))
    has_movies = bool(re.search(r"/movies/[a-z0-9-]+-\d+", blob))
    info = {"canaryStatus": c["status"], "canaryBlobBytes": len(blob),
            "canaryShowtimes": shows, "canaryHasMovies": has_movies}
    if c["endedInQueue"]:
        return {"ok": False, "reason": "stuck in the Queue-it waiting room (never cleared)", **info}
    if c["status"] and c["status"] >= 400:
        return {"ok": False, "reason": f"canary page returned HTTP {c['status']}", **info}
    if len(blob) < 5000 or not has_movies:
        return {"ok": False,
                "reason": f"canary returned no usable page data (blob {len(blob)} bytes, "
                          f"movies present: {has_movies}) — likely blocked/WAF", **info}
    if shows == 0:
        return {"ok": False,
                "reason": "canary page loaded but parsed 0 showtimes for any movie — "
                          "AMC markup may have changed (parser broken)", **info}
    return {"ok": True, "reason": "", **info}


async def scan() -> dict:
    """One full scan. Returns the same result shape as the JS version."""
    result: dict = {"checkedAtUtc": datetime.now(timezone.utc).isoformat(),
                    "start": START_DATE, "dates": [], "finds": [], "errors": []}
    sem = asyncio.Semaphore(3)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            ignore_https_errors=True, user_agent=UA,
            viewport={"width": 1200, "height": 1400},
            locale="en-US", timezone_id="America/Los_Angeles",
        )
        try:
            result["health"] = await health_check(ctx)

            async def one_date(d: str) -> dict:
                async with sem:
                    try:
                        url = (f"https://www.amctheatres.com/movie-theatres/{THEATER_PATH}"
                               f"/showtimes/all/{d}/{THEATER}/all")
                        r = await fetch_html(ctx, url)
                        blob = rsc_blob(r["html"])
                        shows = [s for s in parse_showtimes(blob)
                                 if MOVIE in s["aria"] and FORMAT in s["aria"]]
                        night = [{"id": s["id"], "time": s["time"], "status": s["status"],
                                  "utc": s["utc"]}
                                 for s in shows if is_night(s)]
                        return {"date": d, "total": len(shows), "night": night}
                    except Exception as e:  # noqa: BLE001
                        result["errors"].append(f"showtimes {d}: {e}")
                        return {"date": d, "total": 0, "night": [], "error": True}

            # Probe forward in batches until EMPTY_STOP consecutive dates come
            # back with no shows (past the booking horizon) or MAX_DATES is hit.
            date_iter = gen_dates()
            per_date: list[dict] = []
            empty_streak = 0
            while empty_streak < EMPTY_STOP and len(per_date) < MAX_DATES:
                batch = [next(date_iter)
                         for _ in range(min(6, MAX_DATES - len(per_date)))]
                for r in await asyncio.gather(*(one_date(d) for d in batch)):
                    per_date.append(r)
                    if r.get("error"):
                        continue  # transient failure: don't let it end the probe
                    empty_streak = 0 if r["total"] > 0 else empty_streak + 1
                    if empty_streak >= EMPTY_STOP:
                        break
            # drop trailing horizon-probe dates that had nothing
            while per_date and not per_date[-1].get("error") and per_date[-1]["total"] == 0:
                per_date.pop()
            result["dates"] = per_date
            result["datesChecked"] = [d["date"] for d in per_date]

            now = datetime.now(timezone.utc)

            def upcoming(s: dict) -> bool:
                try:
                    return parse_show_utc(s["utc"]) > now
                except ValueError:
                    return True

            to_check = [{"date": d["date"], **s}
                        for d in result["dates"] for s in d["night"]
                        if not re.search(r"sold\s*out", s["status"], re.I) and upcoming(s)]
            result["seatChecks"] = len(to_check)

            async def one_seatmap(s: dict) -> None:
                async with sem:
                    try:
                        r = await fetch_html(ctx, BOOK_URL.format(id=s["id"]), "seatingLayout")
                        seats = parse_seat_layout(rsc_blob(r["html"]))
                        if seats is None:
                            result["errors"].append(f"no layout {s['date']} {s['time']}")
                            return
                        pairs = find_pairs(seats)
                        if pairs:
                            result["finds"].append({
                                "date": s["date"], "time": s["time"], "status": s["status"],
                                "id": s["id"], "bestPairs": pairs[:4], "pairCount": len(pairs),
                            })
                    except Exception as e:  # noqa: BLE001
                        result["errors"].append(f"seats {s['date']} {s['time']}: {e}")

            await asyncio.gather(*(one_seatmap(s) for s in to_check))
        finally:
            await ctx.close()
            await browser.close()

    result["finds"].sort(key=lambda f: f["date"] + f["time"])
    return result


def find_signature(f: dict) -> str:
    return ",".join(sorted(p["seats"][0] + "+" + p["seats"][1] for p in f["bestPairs"]))


def format_find(f: dict) -> str:
    best = ", ".join(f"{p['seats'][0]}+{p['seats'][1]} (row {p['row']})" for p in f["bestPairs"])
    return (
        f"*The Odyssey — IMAX 70mm pairs @ AMC Metreon*\n"
        f"{f['date']} {f['time']}  [{f['status']}]\n"
        f"{f['pairCount']} pair(s) row {MIN_ROW}+; best: {best}\n"
        f"[Book seats]({BOOK_URL.format(id=f['id'])})\n"
        f"{source_tag()}"
    )


def alert(result: dict, cfg: Config, state: State) -> int:
    """Send Telegram alerts for new/changed finds + health transitions.
    Returns number of messages sent."""
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)
    chat_ids = state.active_subscribers()
    if not chat_ids and cfg.telegram_chat_id:
        chat_ids = [cfg.telegram_chat_id]
    if not chat_ids:
        log.warning("no telegram recipients; skipping alerts")
        return 0

    sent = 0
    health = result.get("health") or {"ok": False, "reason": "health check did not run"}
    prev_health = state.kv_get(KV_HEALTH) or "ok"
    if not health["ok"] and prev_health == "ok":
        tg.fanout(
            f"*Odyssey watcher BROKEN*\n{health['reason']}\n"
            f"\"no seats\" can no longer be trusted until this recovers.\n{source_tag()}",
            chat_ids)
        state.kv_set(KV_HEALTH, "broken")
        sent += 1
    elif health["ok"] and prev_health == "broken":
        tg.fanout(f"*Odyssey watcher recovered* — scans are trustworthy again.\n{source_tag()}",
                  chat_ids)
        state.kv_set(KV_HEALTH, "ok")
        sent += 1
    if not health["ok"]:
        return sent  # finds are empty/untrustworthy when broken

    alerted: dict = json.loads(state.kv_get(KV_ALERTED) or "{}")
    today = date.today().isoformat()
    alerted = {k: v for k, v in alerted.items() if v.get("date", "9999") >= today}
    fresh = []
    for f in result["finds"]:
        sig = find_signature(f)
        if alerted.get(f["id"], {}).get("sig") == sig:
            continue
        fresh.append(f)
        alerted[f["id"]] = {"sig": sig, "date": f["date"]}
    if len(fresh) <= 3:
        for f in fresh:
            tg.fanout(format_find(f), chat_ids)
            sent += 1
    elif fresh:
        # many shows changed at once (e.g. a new booking week opened) —
        # one digest instead of a message barrage
        lines = [f"*The Odyssey — IMAX 70mm pairs @ AMC Metreon ({len(fresh)} shows)*"]
        for f in fresh:
            best = ", ".join(f"{p['seats'][0]}+{p['seats'][1]}" for p in f["bestPairs"][:2])
            lines.append(f"{f['date']} {f['time']} — {f['pairCount']} pair(s), "
                         f"best {best} — [book]({BOOK_URL.format(id=f['id'])})")
        lines.append(source_tag())
        tg.fanout("\n".join(lines), chat_ids)
        sent += 1
    state.kv_set(KV_ALERTED, json.dumps(alerted))
    return sent


def print_summary(result: dict) -> None:
    print("<RESULT>" + json.dumps(result) + "</RESULT>")
    print("\n=== Odyssey IMAX 70mm — non-front-row pairs watch ===")
    dc = result.get("datesChecked", [])
    span = f"{dc[0]}..{dc[-1]} ({len(dc)} dates)" if dc else "none"
    print("Checked at:", result["checkedAtUtc"], "| dates:", span)
    h = result.get("health") or {"ok": False, "reason": "health check did not run"}
    if not h["ok"]:
        print(f"HEALTH: BROKEN — {h['reason']}")
        print("RESULT: UNKNOWN — the watcher is blocked or broken, so \"no seats\" "
              "cannot be trusted. This needs attention.")
        return
    print(f"HEALTH: OK (canary showtimes: {h['canaryShowtimes']})")
    if not result["finds"]:
        any_shows = any(d["total"] > 0 for d in result["dates"])
        print("RESULT: NONE — shows are listed but no two-together non-front-row "
              "seats are available." if any_shows else
              "RESULT: NONE — no Odyssey IMAX 70mm night showtimes on sale yet for these dates.")
    else:
        print(f"RESULT: FOUND {len(result['finds'])} show(s) with non-front-row pairs:")
        for f in result["finds"]:
            p = ", ".join(f"{x['seats'][0]}+{x['seats'][1]} (row {x['row']})"
                          for x in f["bestPairs"])
            print(f"  {f['date']} {f['time']} [{f['status']}] — {f['pairCount']} pair(s); best: {p}")
    if result["errors"]:
        print("Notes:", " | ".join(result["errors"]))


def run_once(cfg: Config, no_telegram: bool) -> dict:
    result = asyncio.run(scan())
    print_summary(result)
    if not no_telegram:
        state = State(cfg.state_db)
        try:
            n = alert(result, cfg, state)
            log.info("odyssey: %d telegram message(s) sent", n)
        finally:
            state.close()
    return result


def cli() -> None:
    parser = argparse.ArgumentParser(description="Odyssey IMAX 70mm seat watcher")
    parser.add_argument("--loop", type=int, metavar="SECONDS",
                        help="rescan every N seconds instead of exiting")
    parser.add_argument("--no-telegram", action="store_true",
                        help="print only; do not send alerts")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.load()
    while True:
        result = None
        try:
            result = run_once(cfg, args.no_telegram)
        except Exception:  # noqa: BLE001
            log.exception("odyssey scan failed")
        if not args.loop:
            break
        time.sleep(next_delay(result, args.loop))


# Minutes before each showtime to force a precisely-timed scan. 60 = an hour
# out; 30 = AMC's refund cutoff, when the last cancellations land.
PRESHOW_SWEEPS_MIN = (60, 30)


def next_delay(result: dict | None, loop_seconds: int) -> float:
    """Regular cadence, shortened so scans fire at T-60 and T-30 before each
    upcoming showtime (the last-minute-cancellation window)."""
    delay = float(loop_seconds)
    if not result:
        return delay
    now = datetime.now(timezone.utc)
    for d in result.get("dates", []):
        for s in d.get("night", []):
            try:
                start = parse_show_utc(s["utc"])
            except (ValueError, KeyError):
                continue
            for mins in PRESHOW_SWEEPS_MIN:
                wait = (start - timedelta(minutes=mins) - now).total_seconds()
                if 0 < wait < delay:
                    delay = max(wait, 60.0)
                    log.info("odyssey: next scan in %.0fs — T-%dmin before %s %s",
                             delay, mins, d["date"], s["time"])
    return delay


if __name__ == "__main__":
    cli()
