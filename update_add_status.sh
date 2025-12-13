#!/usr/bin/env bash
set -euo pipefail

BASE="/home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot"
BOT="$BASE/bot.py"

ts="$(date +%Y%m%d_%H%M%S)"
cp "$BOT" "$BOT.bak_$ts"

echo "[OK] Backup created: bot.py.bak_$ts"

# ---------- helpers ----------
add_after() {
  local pattern="$1"
  local block="$2"
  grep -qF "$block" "$BOT" && return 0
  awk -v pat="$pattern" -v add="$block" '
    $0 ~ pat && !done {
      print $0
      print add
      done=1
      next
    }
    {print}
  ' "$BOT" > "$BOT.tmp" && mv "$BOT.tmp" "$BOT"
}

append_if_missing() {
  local marker="$1"
  local block="$2"
  grep -qF "$marker" "$BOT" || echo -e "\n$block" >> "$BOT"
}

# ---------- imports ----------
append_if_missing "import time" "import time"
append_if_missing "import platform" "import platform"

# ---------- globals ----------
append_if_missing "START_TS =" "START_TS = int(time.time())"
append_if_missing "PAID_MODE =" "PAID_MODE = False  # temporary flag"

# ---------- helper functions ----------
append_if_missing "def fmt_uptime" '
def fmt_uptime(seconds):
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return "%dh %dm %ds" % (h, m, s)
    if m:
        return "%dm %ds" % (m, s)
    return "%ds" % s
'

append_if_missing "def heartbeat_age" '
def heartbeat_age():
    try:
        with open(DATA_DIR / "heartbeat.txt", "r") as f:
            ts = int(f.read().strip())
        return int(time.time()) - ts
    except Exception:
        return None
'

# ---------- status handler ----------
append_if_missing "def status_handler" '
def status_handler(update, context):
    pid = os.getpid()
    uptime = int(time.time()) - START_TS
    hb_age = heartbeat_age()

    kb_files = 0
    try:
        for p in (KB_TEXT_DIR.glob("*.txt"), KB_TEXT_DIR.glob("*.md")):
            kb_files += len(list(p))
    except Exception:
        pass

    text = (
        "🤖 AiAntiblokBot\n"
        "🆔 PID: {pid}\n"
        "⏱ Uptime: {uptime}\n"
        "❤️ Heartbeat age: {hb}\n"
        "📚 KB files: {kb}\n"
        "⚙️ Mode: {mode}\n"
        "🐍 Python: {py}"
    ).format(
        pid=pid,
        uptime=fmt_uptime(uptime),
        hb=("%ss" % hb_age if hb_age is not None else "n/a"),
        kb=kb_files,
        mode=("PAID" if PAID_MODE else "FREE"),
        py=platform.python_version(),
    )

    update.message.reply_text(text)
'

# ---------- register handler ----------
grep -q 'CommandHandler("status"' "$BOT" || \
sed -i '/CommandHandler("start"/a\    dp.add_handler(CommandHandler("status", status_handler))' "$BOT"

echo "[OK] /status handler added"
echo "[OK] bot.py updated successfully"
