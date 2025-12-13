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

# If on_error already exists -> nothing
if re.search(r"^def\s+on_error\s*\(", s, flags=re.M):
    print("OK: on_error already exists")
else:
    # Insert right before def main()
    m = re.search(r"^def\s+main\s*\(", s, flags=re.M)
    if not m:
        raise SystemExit("def main() not found")

    on_error_fn = (
"\n\ndef on_error(update, context):\n"
"    \"\"\"Global error handler for python-telegram-bot v12 (Python 3.6).\"\"\"\n"
"    try:\n"
"        logging.exception('Unhandled exception in handler: %s', getattr(context, 'error', None))\n"
"    except Exception:\n"
"        pass\n"
"\n"
    )
    s = s[:m.start()] + on_error_fn + s[m.start():]
    p.write_text(s, encoding='utf-8')
    print("OK: on_error added")

PY

"$VENV_PY" -m py_compile "$BOT"
echo "[OK] venv py_compile passed"

pkill -f "AiAntiblokBot.*bot\.py" || true
sleep 1
pkill -9 -f "AiAntiblokBot.*bot\.py" || true
rm -f "$BASE/data/bot.pid"

"$BASE/data/watchdog.sh"

echo "[OK] restarted"
pgrep -af "AiAntiblokBot.*bot\.py" || true
echo "---- last 20 bot.log ----"
tail -n 20 "$BASE/logs/bot.log" || true
