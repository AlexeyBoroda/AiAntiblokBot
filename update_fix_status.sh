#!/usr/bin/env bash
set -euo pipefail

BASE="/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot"
BOT="$BASE/bot.py"
TS="$(date +%Y%m%d_%H%M%S)"
BAK="$BASE/bot.py.bak_${TS}"

cp -a "$BOT" "$BAK"
echo "[OK] Backup: $BAK"

python3 - <<'PY'
from pathlib import Path
import re

bot_path = Path("/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot/bot.py")
s = bot_path.read_text(encoding="utf-8", errors="ignore")

changed = False

# 1) Добавим error handler функцию (если нет)
if "def on_error(update, context):" not in s:
    insert_point = None
    # вставим перед status_handler, если он есть
    m = re.search(r"\n(def\s+status_handler\s*\()", s)
    if m:
        insert_point = m.start(1)
    else:
        # иначе — в конец перед if __name__
        m2 = re.search(r"\nif\s+__name__\s*==\s*[\"']__main__[\"']\s*:", s)
        insert_point = m2.start(0) if m2 else len(s)

    block = (
        "\n\ndef on_error(update, context):\n"
        "    try:\n"
        "        logging.exception(\"AiAntiblokBot: unhandled error\", exc_info=context.error)\n"
        "    except Exception:\n"
        "        pass\n"
    )
    s = s[:insert_point] + block + s[insert_point:]
    changed = True

# 2) В main(): добавить dp.add_error_handler(on_error) если нет
if "add_error_handler(on_error)" not in s:
    # найдём место после создания dp = updater.dispatcher (или similar)
    # и вставим до add_handler(...)
    m = re.search(r"(dp\s*=\s*updater\.dispatcher\s*\n)", s)
    if m:
        pos = m.end(1)
        s = s[:pos] + "    dp.add_error_handler(on_error)\n" + s[pos:]
        changed = True

# 3) Fallback regex handler для /status(@бот)
# Вставляем СРАЗУ после CommandHandler("status", ...)
if "Filters.regex(r\"^/status" not in s:
    pat = r"(dp\.add_handler\(CommandHandler\(\"status\",\s*status_handler\)\)\s*\n)"
    m = re.search(pat, s)
    if m:
        pos = m.end(1)
        s = s[:pos] + "    dp.add_handler(MessageHandler(Filters.regex(r\"^/status(@\\w+)?$\"), status_handler))\n" + s[pos:]
        changed = True

# 4) Логирование входа в status_handler (если ещё нет)
if "AiAntiblokBot: /status from" not in s:
    m = re.search(r"(def\s+status_handler\s*\(\s*update\s*,\s*context\s*\)\s*:\s*\n)", s)
    if m:
        pos = m.end(1)
        inject = (
            "    try:\n"
            "        u = update.effective_user\n"
            "        logging.info(\"AiAntiblokBot: /status from %s (%s)\", getattr(u,'username',None), getattr(u,'id',None))\n"
            "    except Exception:\n"
            "        pass\n"
        )
        s = s[:pos] + inject + s[pos:]
        changed = True

bot_path.write_text(s, encoding="utf-8")
print("[OK] Updated:", bool(changed))
PY

echo "[OK] bot.py patched"

