#!/bin/bash
# Start the Polymarket bot in the background — safe to close your terminal after running this.
# Logs go to bot.log in the same folder.
# To stop: run stop_bot.sh or kill the PID shown below.

cd "$(dirname "$0")"
LOG_FILE="$(pwd)/bot.log"
PID_FILE="$(pwd)/bot.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Bot is already running (PID $(cat "$PID_FILE")). Stop it first with stop_bot.sh"
  exit 1
fi

source .venv/bin/activate
nohup python main.py > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "Bot started in background. PID: $(cat "$PID_FILE")"
echo "Dashboard: http://localhost:8080"
echo "Logs:      tail -f $LOG_FILE"
echo "Stop:      bash $(pwd)/stop_bot.sh"
