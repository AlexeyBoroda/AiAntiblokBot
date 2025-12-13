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

# 1) Extract and remove any existing __main__ block (anywhere)
main_block = ""
m = re.search(r"(?ms)^\s*if\s+__name__\s*==\s*['\"]__main__['\"]\s*:\s*\n(?:[ \t].*\n?)+", s)
if m:
    main_block = m.group(0).rstrip() + "\n"
    s = s[:m.start()] + s[m.end():]

# 2) Remove any existing def on_error(...) block at top-level
# (def on_error ... until next top-level def/class or EOF)
s = re.sub(r"(?ms)^def\s+on_error\s*\(.*?\)\s*:\s*\n(?:^[ \t].*\n?)*", "", s)

# 3) Ensure we have a clean on_error inserted BEFORE def main()
m2 = re.search(r"^def\s+main\s*\(", s, flags=re.M)
if not m2:
    raise SystemExit("def main() not found")

on_error_fn = (
"\n\ndef on_error(update, context):\n"
"    \"\"\"Global error handler (python-telegram-bot v12 / Python 3.6 compatible).\"\"\"\n"
"    try:\n"
"        import logging\n"
"        logging.exception('Unhandled exception: %s', getattr(context, 'error', None))\n"
"    except Exception:\n"
"        pass\n"
)

s = s[:m2.start()] + on_error_fn + s[m2.start():]

# 4) Re-append __main__ block to the VERY end (or create if missing)
if not main_block:
    main_block = "\n\nif __name__ == \"__main__\":\n    main()\n"
else:
    # normalize to call main()
    if "main()" not in main_block:
        main_block = "\n\nif __name__ == \"__main__\":\n    main()\n"
    else:
        if not main_block.endswith("\n"):
            main_block += "\n"
        main_block = "\n\n" + main_block

s = s.rstrip() + main_block

p.write_text(s, encoding="utf-8")
print("OK: on_error moved before main, __main__ block moved to EOF")
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
echo "---- last 30 bot.log ----"
tail -n 30 "$BASE/logs/bot.log" || true
