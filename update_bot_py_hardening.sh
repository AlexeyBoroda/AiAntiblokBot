#!/usr/bin/env bash
set -euo pipefail

BASE="/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot"
cd "$BASE"

BOT="$BASE/bot.py"
TS="$(date +%Y%m%d_%H%M%S)"
BAK="$BASE/bot.py.bak_${TS}"

cp -f "$BOT" "$BAK"
echo "[OK] backup: $BAK"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot/bot.py")
src = p.read_text(encoding="utf-8", errors="ignore")

# 1) Remove drop_pending_updates arg (old PTB doesn't support it)
src2 = re.sub(r"updater\.start_polling\s*\(\s*drop_pending_updates\s*=\s*True\s*\)", "updater.start_polling()", src)
src2 = re.sub(r"updater\.start_polling\s*\(\s*\)", "updater.start_polling()", src2)  # normalize

# 2) Replace status_handler with safe version (prevents broken multiline strings)
m = re.search(r"^def\s+status_handler\s*\(.*?\):\s*\n", src2, flags=re.M)
if m:
    start = m.start()
    m2 = re.search(r"^def\s+\w+\s*\(", src2[m.end():], flags=re.M)
    end = m.end() + (m2.start() if m2 else len(src2))

    new_block = (
        "def status_handler(update, context):\n"
        "    \"\"\"Health/status command. Safe formatting for Py3.6.\"\"\"\n"
        "    try:\n"
        "        pid = os.getpid()\n"
        "        uptime = int(time.time()) - START_TS\n"
        "        hb_age = heartbeat_age()\n"
        "        kb_files = 0\n"
        "        try:\n"
        "            for pattern in ('*.txt', '*.md'):\n"
        "                kb_files += len(list(KB_TEXT_DIR.glob(pattern)))\n"
        "        except Exception:\n"
        "            pass\n"
        "        lines = [\n"
        "            '🤖 AiAntiblokBot',\n"
        "            '🆔 PID: {}'.format(pid),\n"
        "            '⏱ Uptime: {}'.format(fmt_uptime(uptime)),\n"
        "            '❤️ Heartbeat age: {}'.format(('%ss' % hb_age) if hb_age is not None else 'n/a'),\n"
        "            '📚 KB files: {}'.format(kb_files),\n"
        "            '⚙️ Mode: {}'.format('PAID' if PAID_MODE else 'FREE'),\n"
        "            '🐍 Python: {}'.format(platform.python_version()),\n"
        "        ]\n"
        "        update.message.reply_text('\\n'.join(lines))\n"
        "    except Exception as e:\n"
        "        try:\n"
        "            update.message.reply_text('status error: {}'.format(e))\n"
        "        except Exception:\n"
        "            pass\n"
        "\n"
    )
    src2 = src2[:start] + new_block + src2[end:]
    print("OK: status_handler replaced")
else:
    print("WARN: status_handler not found, only start_polling fixed")

p.write_text(src2, encoding="utf-8")
print("OK: bot.py patched")
PY

# 3) sanity checks: show the exact line with start_polling
echo "[INFO] start_polling lines:"
grep -n "start_polling" "$BOT" || true

# 4) compile check (must pass)
python3 -m py_compile "$BOT"
echo "[OK] py_compile passed"

# 5) restart bot via watchdog
pkill -f "AiAntiblokBot.*bot\.py" || true
sleep 1
pkill -9 -f "AiAntiblokBot.*bot\.py" || true
rm -f "$BASE/data/bot.pid"
"$BASE/data/watchdog.sh"

echo "[OK] restarted via watchdog"
echo "[INFO] running processes:"
pgrep -af "AiAntiblokBot.*bot\.py" || true

echo "[INFO] last bot.log lines:"
tail -n 30 "$BASE/logs/bot.log" || true
