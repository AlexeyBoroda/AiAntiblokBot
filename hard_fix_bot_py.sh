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

# 1) Ensure on_error exists (define before main)
has_add = re.search(r"dp\.add_error_handler\(\s*on_error\s*\)", s) is not None
has_def = re.search(r"^def\s+on_error\s*\(", s, flags=re.M) is not None

on_error_block = (
"\n"
"def on_error(update, context):\n"
"    \"\"\"Global error handler.\"\"\"\n"
"    try:\n"
"        logging.exception('Unhandled error: %s', getattr(context, 'error', None))\n"
"    except Exception:\n"
"        pass\n"
"\n"
)

if has_add and not has_def:
    m = re.search(r"^def\s+main\s*\(", s, flags=re.M)
    if m:
        s = s[:m.start()] + on_error_block + s[m.start():]
    else:
        s = on_error_block + s

# 2) Hard-replace status_handler with safe implementation (no broken string literals)
status_re = re.compile(r"^def\s+status_handler\s*\(.*?\):\n(?:^[ \t].*\n)*", re.M)
new_status = (
"def status_handler(update, context):\n"
"    pid = os.getpid()\n"
"    uptime = int(time.time()) - START_TS\n"
"    hb_age = heartbeat_age()\n"
"\n"
"    kb_files = 0\n"
"    try:\n"
"        for pat in ('*.txt', '*.md'):\n"
"            kb_files += len(list(KB_TEXT_DIR.glob(pat)))\n"
"    except Exception:\n"
"        pass\n"
"\n"
"    lines = []\n"
"    lines.append('🤖 AiAntiblokBot')\n"
"    lines.append('🆔 PID: %s' % pid)\n"
"    lines.append('⏱ Uptime: %s' % fmt_uptime(uptime))\n"
"    lines.append('❤️ Heartbeat age: %s' % (('%ss' % hb_age) if hb_age is not None else 'n/a'))\n"
"    lines.append('📚 KB files: %s' % kb_files)\n"
"    lines.append('⚙️ Mode: %s' % ('PAID' if PAID_MODE else 'FREE'))\n"
"    lines.append('🐍 Python: %s' % platform.python_version())\n"
"    text = '\\n'.join(lines)\n"
"    update.message.reply_text(text)\n"
"\n"
)

if status_re.search(s):
    s = status_re.sub(new_status, s)
else:
    # if handler exists but formatting unexpected: append at end
    s += "\n\n" + new_status

# 3) Make sure /status handler is registered (and before text MessageHandler)
# If missing - add next to other CommandHandler
if "CommandHandler(\"status\"" not in s and "CommandHandler('status'" not in s:
    s = re.sub(
        r"(dp\.add_handler\(CommandHandler\(\"help\".*?\)\)\s*\n)",
        r"\1    dp.add_handler(CommandHandler(\"status\", status_handler))\n",
        s,
        flags=re.S
    )

# If text handler is before status handler in add_handler section -> move status above MessageHandler
# (simple approach: ensure status add_handler line placed before MessageHandler line)
lines = s.splitlines(True)
out = []
status_lines = []
for line in lines:
    if re.search(r"dp\.add_handler\(CommandHandler\([\"']status[\"']\s*,\s*status_handler\)\)", line):
        status_lines.append(line)
    else:
        out.append(line)
s2 = "".join(out)
if status_lines:
    # remove any existing then insert before first MessageHandler(Filters.text...)
    s2_lines = s2.splitlines(True)
    out2 = []
    inserted = False
    for line in s2_lines:
        if (not inserted) and ("dp.add_handler(MessageHandler" in line):
            out2.extend(status_lines)
            inserted = True
        out2.append(line)
    if not inserted:
        out2.extend(status_lines)
    s = "".join(out2)
else:
    s = s2

p.write_text(s, encoding="utf-8")
print("OK: bot.py patched")
PY

# compile with the SAME python that runs the bot (venv python 3.6)
"$VENV_PY" -m py_compile "$BOT"
echo "[OK] venv py_compile passed"

# restart cleanly
pkill -f "AiAntiblokBot.*bot\.py" || true
sleep 1
pkill -9 -f "AiAntiblokBot.*bot\.py" || true
rm -f "$BASE/data/bot.pid"

# optional: clear bot.log tail confusion
# : > "$BASE/logs/bot.log"

"$BASE/data/watchdog.sh"

echo "[OK] restarted"
pgrep -af "AiAntiblokBot.*bot\.py" || true
echo "---- last 30 bot.log ----"
tail -n 30 "$BASE/logs/bot.log" || true
