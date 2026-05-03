#!/usr/bin/env bash
# Start a long-lived Chrome with a remote debugging port and a dedicated
# profile directory. The Python script attaches via CDP, so this Chrome
# acts as the persistent "session" that survives across runs (cookies,
# fingerprint, optional manual logins).
#
# After first launch, you can browse to viagogo.com / axs.com once in
# this Chrome to clear any anti-bot challenge — the session persists.
#
# Re-run anytime: `./start_chrome.sh`. Use `./stop_chrome.sh` to halt.

set -euo pipefail
cd "$(dirname "$0")"

PROFILE_DIR="${MOVEMENTTIX_CHROME_PROFILE:-$HOME/.movementtix-chrome}"
PORT="${MOVEMENTTIX_CHROME_PORT:-9222}"
PIDFILE="chrome.pid"

# Pick the first chrome we can find.
CANDIDATES=(
  "${MOVEMENTTIX_CHROME_BIN:-}"
  "$(command -v google-chrome 2>/dev/null || true)"
  "$(command -v chromium 2>/dev/null || true)"
  "$(command -v chromium-browser 2>/dev/null || true)"
  "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
CHROME=""
for c in "${CANDIDATES[@]}"; do
  if [[ -n "$c" && -x "$c" ]]; then CHROME="$c"; break; fi
done
if [[ -z "$CHROME" ]]; then
  echo "no chrome binary found — set MOVEMENTTIX_CHROME_BIN" >&2
  exit 1
fi

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "chrome already running (pid $(cat "$PIDFILE")); CDP at http://localhost:$PORT"
  exit 0
fi

mkdir -p "$PROFILE_DIR"
echo "starting $CHROME"
echo "  profile : $PROFILE_DIR"
echo "  CDP port: $PORT"

nohup "$CHROME" \
  --headless=new \
  --disable-gpu \
  --no-sandbox \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --ignore-certificate-errors \
  --disable-blink-features=AutomationControlled \
  --disable-features=IsolateOrigins,site-per-process \
  --lang=en-US \
  --user-agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15" \
  --remote-debugging-port="$PORT" \
  --remote-allow-origins=* \
  --user-data-dir="$PROFILE_DIR" \
  --window-size=1366,900 \
  about:blank \
  >> chrome.log 2>&1 &
echo $! > "$PIDFILE"

# Wait up to 30s for the DevTools HTTP endpoint to come up.
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "chrome ready (pid $(cat "$PIDFILE")). cdp_url: http://localhost:$PORT"
    exit 0
  fi
  sleep 0.5
done
echo "chrome did not open CDP port within 30s — check chrome.log" >&2
exit 1
