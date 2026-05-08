#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f listener.pid ]]; then
  echo "no listener.pid"
  exit 0
fi
PID="$(cat listener.pid)"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "sent SIGTERM to pid $PID (allow ~25s for in-flight long-poll to exit)"
fi
rm -f listener.pid
