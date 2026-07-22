#!/usr/bin/env bash
# Start the Odyssey IMAX 70mm seat watcher as a background daemon.
# Rescans every 15 minutes; alerts fan out to the Telegram subscriber list.
# Stop with: kill $(cat odyssey.pid)
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f odyssey.pid ]] && kill -0 "$(cat odyssey.pid)" 2>/dev/null; then
  echo "odyssey watcher already running (pid $(cat odyssey.pid))"
  exit 1
fi

nohup .venv/bin/python -u -m movementtix.odyssey --loop 900 >> odyssey.log 2>&1 &
echo $! > odyssey.pid
echo "started (pid $(cat odyssey.pid)). tail -f odyssey.log"
