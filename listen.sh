#!/usr/bin/env bash
# Always-on Telegram command listener. Runs as its own process so /status
# replies stay instant regardless of scraper state. No Chrome dependency.
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f listener.pid ]] && kill -0 "$(cat listener.pid)" 2>/dev/null; then
  echo "listener already running (pid $(cat listener.pid))"
  exit 0
fi

# Prefer the project venv's python if present.
if [[ -x .venv/bin/python ]]; then
  PY=".venv/bin/python"
else
  PY="python"
fi

nohup "$PY" -m movementtix.listener >> listener.log 2>&1 &
echo $! > listener.pid
echo "listener started (pid $(cat listener.pid)). tail -f listener.log"
