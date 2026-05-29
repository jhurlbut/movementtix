# movementtix

Telegram bot + scraper that tracks Movement Music Festival (Detroit, Memorial Day weekend) ticket prices across Tixel, Eventim, StubHub, Viagogo, VividSeats. Stack: SQLite (WAL) + Playwright over a CDP-attached Chrome relay + httpx for sites that segment inventory by UA/cookies (Tixel). Always-on Telegram long-poll listener runs as a separate process so commands stay instant regardless of scraper state. Per-subscriber watchlist (`subscribers_watch`) + per-subscriber Reddit-feed toggle (`subscribers.reddit_alerts`).

Local stack: `./run.sh` brings up Chrome relay + listener + scraper. `./stop_chrome.sh`, `./stop_listener.sh`, and `kill $(cat movementtix.pid)` take it down. The GH Actions cron workflow is disabled manually — local-only deployment.

## 2026 run summary (May 6 – May 29)

Festival was 2026-05-23 to 2026-05-25 (Sat–Mon).

**Volume**
- 23 days running, 5,615 scraper cycles, 61,769 listings recorded
- 55 price alerts + 21 r/MovementDEMF post alerts fired (1 subscriber)
- 410 Reddit posts processed total since the unauth feed went live 2026-05-18

**Bot didn't crash, but 4 of 5 scrapers silently died**

The Chrome relay had been running 11 days when the CDP-dependent scrapers (eventim, stubhub, viagogo, vividseats) all timed out at the 180s `connect_over_cdp` wait inside a ~19-hour window. Same V8/socket-degradation pattern as the 2026-05-09 incident, just on a different long-running session.

| Site | Last successful fetch | Mode |
|---|---|---|
| vividseats | 2026-05-19 17:52 UTC | CDP |
| eventim | 2026-05-20 13:02 UTC | CDP |
| viagogo | 2026-05-20 13:02 UTC | CDP |
| stubhub | 2026-05-20 13:02 UTC | CDP |
| **tixel** | **2026-05-29 11:43 PT** | **httpx (relay-bypass)** |

The listener and the Tixel scraper kept logging cleanly, so externally the bot looked healthy. From outside, telemetry should have caught this: a "scraper produced 0 rows for site X this cycle" alarm would have flagged it on the first stalled cycle, not 9 days later.

**Tixel was the floor on every pass type, every day.** No other site ever beat it during the full run. The CDP outage cost us cross-platform comparison and Eventim face-value reference, but the headline floor data is intact.

## All-time floors

| Pass | Floor | When | Days to fest |
|---|---|---|---|
| Saturday | **$139.64** | 2026-05-21 16:06 PT | T-2 |
| Sunday | **$142.58** | 2026-05-24 05:54 PT | Day 2 (Sun AM) |
| Monday | **$201.08** | 2026-05-18 10:53 PT | T-5 |
| 3-day | **$251.35** | 2026-05-21 17:21 PT | T-2 |

All on Tixel, all in the final week.

## 3-day trajectory

| Window | 3-day floor | Note |
|---|---|---|
| 5/6–5/8 | $335 → $369 | early flippers, no urgency |
| 5/9–5/15 | $322 – $390 | volatile mid-month drift |
| 5/18 (T-5) | $336 – $385 | modest softening starts |
| 5/19 (T-4) | $357 – $391 | still firm |
| 5/20 (T-3) | $302 | broke $310 for first time |
| **5/21 (T-2)** | **$251 (ATL)** | fire sale |
| 5/22 (T-1) | $279 | absorption |
| 5/23 (Sat) | $279 | sellers won't reduce below gate price |
| 5/24 (Sun) | $391 | holdouts above face |
| 5/25 (Mon) | $335 | |
| 5/26–5/29 | $415 stuck | single stale relisting, post-fest |

## Saturday: the cleanest fire-sale signal

Saturday face value: $227 on Eventim. Tixel single-day Saturday traded at $223–$242 most of the run, then:

- 5/21 (T-2): **$139.64** (39% below face)
- 5/22 (T-1): $212
- 5/23 (Sat): $189.90 → $167 → low $140s
- 5/24+: no Saturday single-day listings

The Saturday/Sunday peer-to-peer fire sale was sharply concentrated in the 48 hours before the festival — consistent with the 2025 anecdotal Reddit data (T-4 $350 → Day 1 $250) we extrapolated from before the run.

## Alert volume by day

```
5/6  – 5/15:  1–8/day   normal background drift
5/18 – 5/20:  3–5/day   softening begins
5/21 – 5/23:  5–11/day  fire sale (peak 11 on 5/22 and 5/23)
5/24 – 5/25:  3–7/day   winding down
```

## Lessons for next year

1. **Restart Chrome relay periodically.** The May 9 OOM was treated as a one-off; it bit us again 11 days later on a different session. A daily or every-other-day `stop_chrome.sh && start_chrome.sh` cron would have eliminated the silent 9-day outage. We chose to defer this on 5/9 ("leave it") — the empirical answer is don't defer.
2. **Alert on silent scraper death.** Each cycle records one row per (site, pass_type). A "site X produced 0 rows for N consecutive cycles" health check would have caught the CDP outage on 5/19 instead of 5/29.
3. **Tixel-only is a viable minimum.** Every all-time low came from Tixel. The other 4 scrapers gave cross-platform context (and confirmed the floor wasn't an outlier) but never set it. If cost or complexity ever becomes an issue, Tixel + Eventim is the minimum viable set.
4. **Fire-sale window is T-2 to T-0, not T-7.** Both 2025 Reddit data and 2026 telemetry agree: meaningful drops don't begin until ~72 hours out. Daily polling frequency only needs to ramp up in the final week.
