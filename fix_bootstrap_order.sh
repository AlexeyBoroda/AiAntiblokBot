#!/usr/bin/env bash
set -euo pipefail

BASE="/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot"
cd "$BASE"

TS="$(date +%Y%m%d_%H%M%S)"
cp -a bot.py "bot.py.bak_${TS}"
echo "[OK] backup: bot.py.bak_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("bot.py")
s = p.read_text(encoding="utf-8", errors="ignore")

# 1) Удаляем ВСЕ блоки if __name__ == "__main__": ... (чтобы не было раннего вызова main())
#    Блок: строка if __name__... и все последующие строки с отступом (и пустые) до следующей "не-отступленной" строки
lines = s.splitlines(True)
out = []
i = 0
removed = 0
while i < len(lines):
    line = lines[i]
    if re.match(r'^\s*if\s+__name__\s*==\s*["\']__main__["\']\s*:\s*$', line):
        removed += 1
        i += 1
        # пропускаем "тело" блока: строки с отступом или пустые
        while i < len(lines) and (lines[i].startswith((" ", "\t")) or lines[i].strip() == ""):
            i += 1
        continue
    out.append(line)
    i += 1
s2 = "".join(out)

# 2) Находим место перед def main(...) и при необходимости вставляем on_error/status_handler
m_main = re.search(r'^\s*def\s+main\s*\(', s2, flags=re.M)
if not m_main:
    raise SystemExit("FATAL: def main(...) не найден в bot.py — не могу безопасно патчить")

insert_pos = m_main.start()

need_on_error = not re.search(r'^\s*def\s+on_error\s*\(', s2, flags=re.M)
need_status   = not re.search(r'^\s*def\s+status_handler\s*\(', s2, flags=re.M)

inserts = []
if need_on_error:
    inserts.append(r'''
def on_error(update, context):
    try:
        import logging, traceback
        logging.exception("Unhandled error: %s", getattr(context, "error", None))
    except Exception:
        pass
'''.lstrip("\n"))

if need_status:
    inserts.append(r'''
def status_handler(update, context):
    import os, time, platform
    pid = os.getpid()
    now = int(time.time())
    try:
        from pathlib import Path
        hb = Path(__file__).resolve().parent / "data" / "heartbeat.txt"
        hb_age = None
        if hb.exists():
            hb_ts = int(hb.read_text(encoding="utf-8", errors="ignore").strip() or "0")
            hb_age = now - hb_ts if hb_ts > 0 else None
    except Exception:
        hb_age = None

    def fmt(sec):
        sec = int(sec)
        d, rem = divmod(sec, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        if d: return f"{d}d {h:02d}:{m:02d}:{s:02d}"
        return f"{h:02d}:{m:02d}:{s:02d}"

    text = (
        "🤖 AiAntiblokBot\n"
        f"🆔 PID: {pid}\n"
        f"⏱ Uptime: {fmt(now - START_TS)}\n"
        f"❤️ Heartbeat age: {hb_age if hb_age is not None else 'n/a'}s\n"
        f"🐍 Python: {platform.python_version()}"
    )
    update.message.reply_text(text)
'''.lstrip("\n"))

if inserts:
    s2 = s2[:insert_pos] + "\n\n" + "\n\n".join(inserts) + "\n\n" + s2[insert_pos:]

# 3) Добавляем __main__ строго в конец
s2 = s2.rstrip() + "\n\nif __name__ == \"__main__\":\n    main()\n"

p.write_text(s2, encoding="utf-8")
print(f"removed __main__ blocks: {removed}")
print(f"inserted on_error: {need_on_error}, inserted status_handler: {need_status}")
PY

echo "[OK] bot.py patched"

# 4) Быстрая проверка компиляции тем же python, что у бота
"$BASE/venv/bin/python" -m py_compile "$BASE/bot.py"
echo "[OK] venv py_compile passed"

# 5) Перезапуск через watchdog
pkill -f "AiAntiblokBot.*bot\.py" || true
sleep 1
pkill -9 -f "AiAntiblokBot.*bot\.py" || true
rm -f "$BASE/data/bot.pid" || true

"$BASE/data/watchdog.sh"
echo "[OK] restarted"

pgrep -af "AiAntiblokBot.*bot\.py" || true
echo "---- last 40 bot.log ----"
tail -n 40 "$BASE/logs/bot.log" || true
