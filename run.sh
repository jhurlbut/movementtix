#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f movementtix.pid ]] && kill -0 "$(cat movementtix.pid)" 2>/dev/null; then
  echo "movementtix already running (pid $(cat movementtix.pid))"
  exit 1
fi

nohup python -m movementtix.main >> movementtix.log 2>&1 &
echo $! > movementtix.pid
echo "started (pid $(cat movementtix.pid)). tail -f movementtix.log"
