#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, time
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

load_dotenv(os.path.join(BASE_DIR, ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME","").strip().lstrip("@")

OUTBOX_PATH = os.getenv("OUTBOX_PATH", os.path.join(DATA_DIR, "outbox.jsonl"))
SENT_PATH = os.path.join(DATA_DIR, "outbox.sent.jsonl")
DIALOGS_PATH = os.getenv("DIALOGS_PATH", os.path.join(DATA_DIR, "dialogs.jsonl"))

TG_API = "https://api.telegram.org/bot{}/".format(BOT_TOKEN)

def append_jsonl(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def send_message(chat_id, text, reply_markup=None):
    url = TG_API + "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    return requests.post(url, data=payload, timeout=20)

def try_dm_then_fallback(user_id, text):
    r = send_message(user_id, text)
    if r.ok:
        return True, "dm"
    rm = None
    if BOT_USERNAME:
        deep = "https://t.me/{}?start=dm".format(BOT_USERNAME)
        rm = {"inline_keyboard":[[{"text":"Открыть личку для ответа", "url": deep}]]}
    r2 = send_message(user_id, text, reply_markup=rm)
    return bool(r2.ok), "fallback"

def main():
    if not BOT_TOKEN or not os.path.exists(OUTBOX_PATH):
        return
    with open(OUTBOX_PATH, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    if not lines:
        return
    # truncate
    open(OUTBOX_PATH, "w", encoding="utf-8").close()

    for ln in lines:
        try:
            evt = json.loads(ln)
        except Exception:
            continue
        if evt.get("type") != "admin_reply":
            continue
        uid = int(evt.get("user_id") or 0)
        text = (evt.get("text") or "").strip()
        if not uid or not text:
            continue

        ok, mode = try_dm_then_fallback(uid, "✉️ Ответ администратора:\n\n" + text)

        evt2 = dict(evt)
        evt2["sent_ts"] = int(time.time())
        evt2["sent_ok"] = bool(ok)
        evt2["sent_mode"] = mode
        append_jsonl(SENT_PATH, evt2)
        append_jsonl(DIALOGS_PATH, {"ts": int(time.time()), "type":"admin_sent", "user_id": uid, "chat_id": uid, "text": text, "ok": bool(ok), "mode": mode})

if __name__ == "__main__":
    main()
