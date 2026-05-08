#!/usr/bin/env bash
# Bring up the full local stack: Chrome relay (CDP) + Telegram listener
# + scraper daemon. Each is its own process; the listener keeps /status
# replies instant even when the scraper is restarted or hung.
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f movementtix.pid ]] && kill -0 "$(cat movementtix.pid)" 2>/dev/null; then
  echo "movementtix already running (pid $(cat movementtix.pid))"
  exit 1
fi

# 1. Chrome relay on :9222 — scrapers attach over CDP for persistent
#    cookies / WAF-warmed session.
if ! curl -fsS http://127.0.0.1:9222/json/version >/dev/null 2>&1; then
  ./start_chrome.sh
fi

# 2. Always-on Telegram command listener.
if [[ ! -f listener.pid ]] || ! kill -0 "$(cat listener.pid 2>/dev/null)" 2>/dev/null; then
  rm -f listener.pid
  ./listen.sh
fi

# 3. Scraper daemon. Wrapped in xvfb-run so force_headed scrapers
#    (StubHub, Eventim) get a display — matches the CI invocation.
if [[ -x .venv/bin/python ]]; then
  PY=".venv/bin/python"
else
  PY="python"
fi

if command -v xvfb-run >/dev/null 2>&1; then
  WRAP=(xvfb-run -a -s "-screen 0 1280x800x24")
else
  echo "WARN: xvfb-run not installed; force_headed scrapers (StubHub/Eventim) will degrade." >&2
  echo "      Install with: sudo apt-get install -y --no-install-recommends xvfb" >&2
  WRAP=()
fi

nohup "${WRAP[@]}" "$PY" -m movementtix.main >> movementtix.log 2>&1 &
echo $! > movementtix.pid
echo "started (pid $(cat movementtix.pid)). tail -f movementtix.log"
