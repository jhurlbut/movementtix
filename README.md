# movementtix

Continuously polls the secondary market for **Movement Music Festival 2026**
(Hart Plaza, Detroit, May 23–25) and pings you on Telegram when a **3-day
pass** or **Saturday single-day pass** drops below your price cap or sets a
new all-time low.

Sites monitored: Tixel · AXS (primary + resale) · StubHub · Viagogo ·
Vivid Seats · SeatGeek.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium    # only if you keep AXS/Viagogo enabled

cp .env.example .env           # then fill in your creds
$EDITOR config.yaml            # tweak caps / poll cadence / which sites
```

### Required env vars (`.env`)

| Name | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID`   | Numeric chat ID for your DM with the bot |
| `SEATGEEK_CLIENT_ID` | Free key from <https://seatgeek.com/account/develop> |

Find your `TELEGRAM_CHAT_ID`: send `/start` to your bot, then run
`python -m movementtix.notify --get-chat-id` and copy the `chat.id`.

### Smoke tests

```bash
pytest                                                # unit tests
python -m movementtix.notify --test                   # send "alive" to Telegram
python -m movementtix.main --once --dry-run           # one full pass, no alerts
python -m movementtix.main --once --dry-run --site seatgeek --pass-type saturday
```

### Run forever (local always-on)

```bash
./run.sh             # nohup; logs to movementtix.log, pid in movementtix.pid
tail -f movementtix.log
kill $(cat movementtix.pid)
```

Optional systemd user unit (auto-restart on reboot):

```ini
# ~/.config/systemd/user/movementtix.service
[Unit]
Description=movementtix ticket tracker

[Service]
WorkingDirectory=%h/movementtix
ExecStart=%h/movementtix/.venv/bin/python -m movementtix.main
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now movementtix.service
```

## Configuration (`config.yaml`)

| Key | Default | Notes |
| --- | --- | --- |
| `caps.three_day` | `300.0` | Alert if 3-day all-in ≤ this |
| `caps.saturday`  | `150.0` | Alert if Saturday all-in ≤ this |
| `poll_seconds.{min,max}` | `300/600` | Per-site jitter range |
| `alert_dedupe_hours` | `6` | Don't repeat the same `(site, price, url)` |
| `sites.<name>` | `true` | Toggle individual scrapers |

Alert rule: send if `total_price ≤ cap` OR `total_price < prior site low`.

## Caveats

- **Viagogo + AXS** sit behind Datadome/Akamai. The scrapers degrade
  gracefully when blocked but you should expect occasional silence from
  these sources. SeatGeek (official API) is the most reliable signal.
- **StubHub / Vivid Seats / Tixel** internal endpoints are unofficial —
  they change without notice. If you start seeing `HTTP 4xx` for one site,
  open the site in DevTools, find the new endpoint, and patch the matching
  scraper in `src/movementtix/scrapers/`.
- Some `EVENT_IDS` in `src/movementtix/config.py` are blank pending a
  one-time lookup (especially Saturday single-day on a few sites). The
  scrapers attempt auto-discovery on first run; copy any IDs they print
  back into `EVENT_IDS` to short-circuit it.
- All-in price = `base + estimated fees` (see `pricing.py`). Fee rates are
  rough; tune them once you observe a real checkout.
- Strictly for personal monitoring. Be a good citizen — don't lower the
  poll interval.

## Layout

```
src/movementtix/
  main.py        # entry + loop + CLI
  config.py      # yaml + .env loader, EVENT_IDS table
  models.py      # Listing, PassType, AlertReason
  state.py       # SQLite history + alert dedupe
  pricing.py     # fee estimator + should_alert rule
  notify.py      # Telegram client (also has --test / --get-chat-id)
  scrapers/
    base.py
    tixel.py axs.py stubhub.py viagogo.py vividseats.py seatgeek.py
tests/
  test_pricing.py test_state.py
```
