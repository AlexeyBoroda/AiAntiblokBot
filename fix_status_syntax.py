from pathlib import Path
import re
from datetime import datetime

p = Path("bot.py")
src = p.read_text(encoding="utf-8", errors="ignore")

# backup
bak = p.with_name(f"bot.py.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
bak.write_text(src, encoding="utf-8")

# Find status_handler block
m = re.search(r"^def\s+status_handler\s*\(.*?\):\s*\n", src, flags=re.M)
if not m:
    raise SystemExit("status_handler not found")

start = m.start()
# naive: replace until next top-level def (next '^def ' at column 0) after this one
m2 = re.search(r"^def\s+\w+\s*\(", src[m.end():], flags=re.M)
end = m.end() + (m2.start() if m2 else len(src))

new_block = r'''def status_handler(update, context):
    """Health/status command. Safe formatting for Py3.6."""
    try:
        pid = os.getpid()
        uptime = int(time.time()) - START_TS
        hb_age = heartbeat_age()
        kb_files = 0
        try:
            for pattern in ("*.txt", "*.md"):
                kb_files += len(list(KB_TEXT_DIR.glob(pattern)))
        except Exception:
            pass

        lines = [
            "🤖 AiAntiblokBot",
            "🆔 PID: {}".format(pid),
            "⏱ Uptime: {}".format(fmt_uptime(uptime)),
            "❤️ Heartbeat age: {}".format(("%ss" % hb_age) if hb_age is not None else "n/a"),
            "📚 KB files: {}".format(kb_files),
            "⚙️ Mode: {}".format("PAID" if PAID_MODE else "FREE"),
            "🐍 Python: {}".format(platform.python_version()),
        ]
        update.message.reply_text("\n".join(lines))
    except Exception as e:
        try:
            update.message.reply_text("status error: {}".format(e))
        except Exception:
            pass
'''

patched = src[:start] + new_block + src[end:]
p.write_text(patched, encoding="utf-8")
print("OK: status_handler replaced safely")
