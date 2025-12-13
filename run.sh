#!/bin/bash
set -e

BASE="/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot"
PY="$BASE/venv/bin/python"
BOT="$BASE/bot.py"
LOG="$BASE/logs/cron_run.log"
LOCK="$BASE/data/bot.lock"

mkdir -p "$BASE/logs" "$BASE/data"

# если бот уже запущен — выходим
if [ -f "$LOCK" ]; then
  PID=$(cat "$LOCK" 2>/dev/null || echo "")
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    exit 0
  fi
fi

# стартуем и пишем pid
nohup "$PY" "$BOT" >> "$LOG" 2>&1 &
echo $! > "$LOCK"
