#!/bin/bash
cd "$(dirname "$0")"
PID_FILE="$(pwd)/bot.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "No PID file found. Bot may not be running."
  exit 1
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  rm "$PID_FILE"
  echo "Bot stopped (PID $PID)."
else
  echo "Bot not running (PID $PID was stale). Cleaning up."
  rm "$PID_FILE"
fi
