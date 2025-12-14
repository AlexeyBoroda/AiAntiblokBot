#!/usr/bin/env bash
set -euo pipefail

# Определяем BASE автоматически (папка на уровень выше data/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BASE"

mkdir -p "$BASE/data" "$BASE/logs"

LOCKFILE="$BASE/data/bot.lock"
PIDFILE="$BASE/data/bot.pid"
LOG_WD="$BASE/logs/watchdog.log"
LOG_BOT="$BASE/logs/bot.log"

ts() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "$(ts) $*" >> "$LOG_WD"; }

exec 9>"$LOCKFILE"
if ! flock -n 9; then
  log "another watchdog instance is running -> exit"
  exit 0
fi

if [[ -s "$PIDFILE" ]]; then
  PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]] && ps -p "$PID" >/dev/null 2>&1; then
    log "pid=$PID running -> ok"
    exit 0
  else
    log "stale pidfile (pid=$PID) -> remove"
    rm -f "$PIDFILE"
  fi
else
  rm -f "$PIDFILE"
fi

log "starting bot..."
nohup "$BASE/venv/bin/python" "$BASE/bot.py" >> "$LOG_BOT" 2>&1 &
NEWPID=$!
echo "$NEWPID" > "$PIDFILE"
log "started pid=$NEWPID"
exit 0
