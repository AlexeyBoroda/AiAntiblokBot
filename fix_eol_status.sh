#!/usr/bin/env bash
set -euo pipefail
BASE="/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot"
cd "$BASE"
BOT="$BASE/bot.py"
VENV_PY="$BASE/venv/bin/python"
TS="$(date +%Y%m%d_%H%M%S)"

cp -f "$BOT" "$BOT.bak_${TS}"
echo "[OK] backup: $BOT.bak_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot/bot.py")
s = p.read_text(encoding="utf-8", errors="ignore")

# 1) Hard replace ANY broken "text = ' ...." pattern inside status_handler area by rebuilding the whole function.
# We'll locate "def status_handler" and replace until the next "def " at column 0.
m = re.search(r"^def\s+status_handler\s*\(.*?\):\n", s, flags=re.M)
if not m:
    raise SystemExit("status_handler not found")

start = m.start()
# end at next top-level def after status_handler
m2 = re.search(r"^def\s+\w+\s*\(.*?\):\n", s[m.end():], flags=re.M)
end = (m.end() + m2.start()) if m2 else len(s)

new_status = (
"def status_handler(update, context):\n"
"    pid = os.getpid()\n"
"    uptime = int(time.time()) - START_TS\n"
"    hb_age = heartbeat_age()\n"
"\n"
"    kb_files = 0\n"
"    try:\n"
"        kb_files = len(list(KB_TEXT_DIR.glob('*.txt'))) + len(list(KB_TEXT_DIR.glob('*.md')))\n"
"    except Exception:\n"
"        pass\n"
"\n"
"    lines = [\n"
"        '🤖 AiAntiblokBot',\n"
"        '🆔 PID: %s' % pid,\n"
"        '⏱ Uptime: %s' % fmt_uptime(uptime),\n"
"        '❤️ Heartbeat age: %s' % (('%ss' % hb_age) if hb_age is not None else 'n/a'),\n"
"        '📚 KB files: %s' % kb_files,\n"
"        '⚙️ Mode: %s' % ('PAID' if PAID_MODE else 'FREE'),\n"
"        '🐍 Python: %s' % platform.python_version(),\n"
"    ]\n"
"    text = \"\\n\".join(lines)\n"
"    update.message.reply_text(text)\n"
"\n"
)

s = s[:start] + new_status + s[end:]

p.write_text(s, encoding="utf-8")
print("OK: status_handler rewritten")
PY

# IMPORTANT: compile with venv python (3.6)
"$VENV_PY" -m py_compile "$BOT"
echo "[OK] venv py_compile passed"

# restart
pkill -f "AiAntiblokBot.*bot\.py" || true
sleep 1
pkill -9 -f "AiAntiblokBot.*bot\.py" || true
rm -f "$BASE/data/bot.pid"

"$BASE/data/watchdog.sh"

echo "[OK] restarted"
pgrep -af "AiAntiblokBot.*bot\.py" || true
echo "---- last 30 bot.log ----"
tail -n 30 "$BASE/logs/bot.log" || true
