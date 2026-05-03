#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f movementtix.pid ]] && kill -0 "$(cat movementtix.pid)" 2>/dev/null; then
  echo "movementtix already running (pid $(cat movementtix.pid))"
  exit 1
fi

# Make sure the Chrome relay is up before launching the tracker.
if ! curl -fsS http://127.0.0.1:9222/json/version >/dev/null 2>&1; then
  ./start_chrome.sh
fi

nohup python -m movementtix.main >> movementtix.log 2>&1 &
echo $! > movementtix.pid
echo "started (pid $(cat movementtix.pid)). tail -f movementtix.log"
