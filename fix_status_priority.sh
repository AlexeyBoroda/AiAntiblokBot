#!/usr/bin/env bash
set -euo pipefail

BASE="/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot"
PY="$BASE/venv/bin/python"
BOT="$BASE/bot.py"

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$BOT" "$BOT.bak_${ts}"
echo "[OK] Backup: $BOT.bak_${ts}"

$PY - <<'PY'
from pathlib import Path
import re

p = Path("/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot/bot.py")
s = p.read_text(encoding="utf-8", errors="ignore")

# 1) Исправляем Filters.text -> Filters.text & ~Filters.command (чтобы команды не съедались)
# Поддержим несколько частых вариантов написания.
patterns = [
    r'MessageHandler\(\s*Filters\.text\s*,',
    r'MessageHandler\(\s*filters\.TEXT\s*,',
    r'MessageHandler\(\s*filters\.text\s*,',
]
changed_filters = False
for pat in patterns:
    if re.search(pat, s):
        s_new = re.sub(pat, lambda m: m.group(0).replace("Filters.text", "Filters.text & ~Filters.command")
                                     .replace("filters.TEXT", "filters.TEXT & ~filters.COMMAND")
                                     .replace("filters.text", "filters.text & ~filters.command"), s)
        if s_new != s:
            s = s_new
            changed_filters = True

# 2) Переставляем регистрацию /status ДО первого MessageHandler (если она стоит ниже)
lines = s.splitlines(True)

# Найдём строку add_handler(CommandHandler("status"...))
status_i = None
for i, ln in enumerate(lines):
    if 'add_handler' in ln and 'CommandHandler("status"' in ln:
        status_i = i
        break

# Найдём первый MessageHandler
msg_i = None
for i, ln in enumerate(lines):
    if "MessageHandler(" in ln:
        msg_i = i
        break

moved = False
if status_i is not None and msg_i is not None and status_i > msg_i:
    status_line = lines.pop(status_i)
    # вставим перед первым MessageHandler
    lines.insert(msg_i, status_line)
    moved = True

s2 = "".join(lines)

# 3) Если /status handler вообще отсутствует — НИЧЕГО не добавляем “вслепую”,
# потому что у тебя он уже есть. Просто проверим.
if 'CommandHandler("status"' not in s2:
    raise SystemExit("Не найден CommandHandler('status') — проверь, что /status вообще есть в bot.py")

p.write_text(s2, encoding="utf-8")
print("OK: filters_fixed=", changed_filters, " status_moved=", moved)
PY

echo "[OK] bot.py updated"

