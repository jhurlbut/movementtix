#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PIDFILE="chrome.pid"
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  kill "$(cat "$PIDFILE")" && rm -f "$PIDFILE"
  echo "chrome stopped"
else
  echo "no running chrome (pidfile missing or stale)"
  rm -f "$PIDFILE"
fi
