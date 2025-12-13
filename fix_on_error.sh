#!/usr/bin/env bash
set -euo pipefail
BASE="/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot"
cd "$BASE"
BOT="$BASE/bot.py"
TS="$(date +%Y%m%d_%H%M%S)"
cp -f "$BOT" "$BOT.bak_${TS}"
echo "[OK] backup: $BOT.bak_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot/bot.py")
s = p.read_text(encoding="utf-8", errors="ignore")

# If dp.add_error_handler(on_error) exists but def on_error missing -> add it.
has_add = re.search(r"dp\.add_error_handler\(\s*on_error\s*\)", s) is not None
has_def = re.search(r"^def\s+on_error\s*\(", s, flags=re.M) is not None

if has_add and not has_def:
    # Insert on_error near other handlers/helpers. Best spot: before main()
    insert_pos = re.search(r"^def\s+main\s*\(", s, flags=re.M)
    if insert_pos:
        pos = insert_pos.start()
    else:
        pos = 0

    block = (
        "\n"
        "def on_error(update, context):\n"
        "    \"\"\"Global error handler for PTB 13.x.\"\"\"\n"
        "    try:\n"
        "        logging.exception('Unhandled error: %s', context.error)\n"
        "    except Exception:\n"
        "        pass\n"
        "    # optional: notify admin/user (kept silent to avoid loops)\n"
        "\n"
    )
    s = s[:pos] + block + s[pos:]
    print("OK: on_error added")
else:
    print("OK: on_error already exists or add_error_handler not present")

p.write_text(s, encoding="utf-8")
PY

python3 -m py_compile "$BOT"
echo "[OK] py_compile passed"

pkill -f "AiAntiblokBot.*bot\.py" || true
sleep 1
pkill -9 -f "AiAntiblokBot.*bot\.py" || true
rm -f "$BASE/data/bot.pid"
"$BASE/data/watchdog.sh"

echo "[OK] restarted"
pgrep -af "AiAntiblokBot.*bot\.py" || true
tail -n 30 "$BASE/logs/bot.log" || true
