#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AiAntiblokBot — MVP (clean)

Функции:
- Доступ к материалам только подписчикам канала https://t.me/Borodulin_expert
- Меню: Раздатка / Шаблон / Курс
- Раздатка и Шаблон берутся из kb/content.json (генерится kb/rebuild_content.py)
- По клику на пункт — отправка файла
- /status — диагностика (можно ограничить ADMIN_IDS)
- Текстовые вопросы:
  - по теме (115-ФЗ/блокировки/комплаенс) — памятка + позже KB+GigaChat
  - не по теме — вежливый отбой
  - мат/агрессия — юморные ответы (рандом)

Совместимо с python-telegram-bot (Updater/Dispatcher, Filters).
Python 3.6+
"""

import os
import re
import json
import time
import random
import logging
import platform
from pathlib import Path
from dotenv import load_dotenv

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, Filters

# -----------------------------
# Paths / config
# -----------------------------
load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
KB_DIR = BASE_DIR / "kb"

CONTENT_JSON = KB_DIR / "content.json"
HEARTBEAT_FILE = DATA_DIR / "heartbeat.txt"

def read_token():
    t = (os.getenv("BOT_TOKEN", "") or "").strip()
    if t:
        return t
    token_file = DATA_DIR / "token.txt"
    if token_file.exists():
        try:
            t2 = token_file.read_text(encoding="utf-8").strip()
            return t2
        except Exception:
            return ""
    return ""

BOT_TOKEN = read_token()
REQUIRED_CHANNEL = (os.getenv("REQUIRED_CHANNEL", "@Borodulin_expert") or "").strip()
PAID_MODE = (os.getenv("PAID_MODE", "0") or "").strip() in ("1", "true", "True", "YES", "yes")

ADMIN_IDS = set()
_admin_raw = (os.getenv("ADMIN_IDS", "") or "").strip()
if _admin_raw:
    try:
        ADMIN_IDS = set(int(x.strip()) for x in _admin_raw.split(",") if x.strip())
    except Exception:
        ADMIN_IDS = set()

START_TS = int(time.time())

# membership cache
_SUB_CACHE = {}          # user_id -> (ts, bool)
SUB_CACHE_TTL = 60       # seconds

# pagination
PAGE_SIZE = 20

# -----------------------------
# Logging / heartbeat
# -----------------------------
def init_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    log_path = LOG_DIR / "bot.log"   # чтобы совпадало с твоим watchdog/tail
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s AiAntiblokBot: %(message)s",
        handlers=[
            logging.FileHandler(str(log_path), encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    logging.info("Logging initialized. pid=%s base=%s", os.getpid(), str(BASE_DIR))

def touch_heartbeat():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(str(int(time.time())), encoding="utf-8")
    except Exception:
        pass

def heartbeat_age():
    try:
        ts = int(HEARTBEAT_FILE.read_text(encoding="utf-8").strip())
        return int(time.time()) - ts
    except Exception:
        return None

def fmt_uptime(seconds):
    if seconds < 60:
        return "%ss" % seconds
    m = seconds // 60
    s = seconds % 60
    if m < 60:
        return "%sm %ss" % (m, s)
    h = m // 60
    m2 = m % 60
    return "%sh %sm" % (h, m2)

# -----------------------------
# Content loading
# -----------------------------
def load_content():
    try:
        raw = CONTENT_JSON.read_text(encoding="utf-8")
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return {"handouts": [], "templates": []}
        obj.setdefault("handouts", [])
        obj.setdefault("templates", [])
        return obj
    except Exception:
        return {"handouts": [], "templates": []}

def safe_resolve_relpath(relpath):
    """
    Разрешаем отдавать файлы только из kb/.
    relpath хранится относительно BASE_DIR, например: "kb/handouts/x.pdf"
    """
    try:
        p = (BASE_DIR / relpath).resolve()
        kb_root = KB_DIR.resolve()
        if str(p).startswith(str(kb_root)) and p.exists() and p.is_file():
            return p
    except Exception:
        pass
    return None

# -----------------------------
# Subscription gate
# -----------------------------
def is_subscriber(bot, user_id):
    now = int(time.time())
    cached = _SUB_CACHE.get(user_id)
    if cached and (now - cached[0] <= SUB_CACHE_TTL):
        return cached[1]

    ok = False
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        status = getattr(member, "status", "") or ""
        ok = status in ("creator", "administrator", "member")
    except Exception:
        ok = False

    _SUB_CACHE[user_id] = (now, ok)
    return ok

def send_subscribe_prompt(chat, bot=None):
    text = (
        "Доступ к материалам — только для подписчиков канала:\n"
        "✅ https://t.me/Borodulin_expert\n\n"
        "Подпишитесь и нажмите «Проверить подписку»."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Подписаться", url="https://t.me/Borodulin_expert")],
        [InlineKeyboardButton("Проверить подписку", callback_data="CHECK_SUB")],
    ])
    chat.reply_text(text, reply_markup=kb)

def gate_or_prompt(update, context):
    uid = update.effective_user.id if update.effective_user else None
    if not uid:
        return False
    if is_subscriber(context.bot, uid):
        return True
    send_subscribe_prompt(update.message, context.bot)
    return False

# -----------------------------
# Humor / moderation
# -----------------------------
HUMOR_VARIANTS = [
    "Мама говорила: ИИ — это не тот, кого так назвали, а тот, кто ведёт себя как ИИ.",
    "Мама говорила: неважно, ИИ ты или ChatGPT — важно, что ты отвечаешь с умом.",
    "Мама говорила: если спрашивают, кто ты — значит, уже неплохо работаешь.",
    "Мама говорила: ярлыки — для коробок. Я — для ответов.",
    "Мама говорила: ИИ — это как коробка конфет. Никогда не знаешь, что спросишь следующим.",
]

_BAD_WORDS = ["сука", "бляд", "хуй", "хуе", "пизд", "еба", "ёба", "нахуй", "мудак", "говно", "идиот"]
def is_abusive(text):
    t = (text or "").lower()
    return any(w in t for w in _BAD_WORDS)

# -----------------------------
# Topic routing (MVP эвристика)
# -----------------------------
TOPIC_KEYWORDS = [
    "блок", "замороз", "115", "комплаенс", "росфин", "счет", "счёт", "карта", "перевод",
    "платеж", "платёж", "дбо", "банк", "огранич", "разблок",
]

def is_on_topic(text):
    t = (text or "").lower()
    return any(k in t for k in TOPIC_KEYWORDS)

def answer_on_topic(update, context):
    msg = (
        "✅ Опишите ситуацию 2–3 фразами: что заблокировали, какая операция, кто контрагент, что ответил банк.\n"
        "✅ Я подскажу план действий и, если нужно, предложу раздаточные материалы.\n\n"
        "✅ Если хотите системно — курс «Как вести бизнес, чтобы не заблокировали счета в банке». Напишите: «Хочу курс»."
    )
    update.message.reply_text(msg)

def answer_off_topic(update, context):
    update.message.reply_text(
        "Я отвечаю по теме блокировок счетов/карт, 115-ФЗ и финансовой безопасности.\n"
        "Если вопрос другой — уточните, как он связан с блокировкой/комплаенсом."
    )

# -----------------------------
# UI: lists / files / courses
# -----------------------------
def _get_section_items(section):
    content = load_content()
    return (content.get(section, []) or [])

def _build_list_keyboard(section, prefix, page):
    items = _get_section_items(section)
    total = len(items)
    if total == 0:
        return None, 0, 0

    max_page = (total - 1) // PAGE_SIZE
    if page < 0:
        page = 0
    if page > max_page:
        page = max_page

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    slice_items = items[start:end]

    rows = []
    for it in slice_items:
        title = it.get("title") or it.get("filename") or it.get("id") or "Файл"
        fid = it.get("id", "")
        rows.append([InlineKeyboardButton(title, callback_data="FILE|%s|%s" % (prefix, fid))])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data="PAGE|%s|%s" % (prefix, page - 1)))
    if page < max_page:
        nav.append(InlineKeyboardButton("Вперёд ➡️", callback_data="PAGE|%s|%s" % (prefix, page + 1)))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("Проверить подписку", callback_data="CHECK_SUB")])

    return InlineKeyboardMarkup(rows), page, max_page

def show_list(update, section, prefix, page=0):
    kb, page, max_page = _build_list_keyboard(section, prefix, page)
    if not kb:
        update.message.reply_text(
            "Пока пусто.\n"
            "Добавь файлы в:\n"
            "• kb/handouts (Раздатка)\n"
            "• kb/templates (Шаблоны)\n"
            "и запусти: python3 kb/rebuild_content.py"
        )
        return

    header = "📎 Раздатка (стр. %s/%s)" % (page + 1, max_page + 1) if prefix == "H" else "🧾 Шаблоны (стр. %s/%s)" % (page + 1, max_page + 1)
    update.message.reply_text(header, reply_markup=kb)

def send_courses(update, context):
    text = (
        "📚 Курсы:\n"
        "1) «Как вести бизнес, чтобы не заблокировали счета» — https://stepik.org/a/252040\n"
        "2) Лид-магнит/бот — https://t.me/BorodulinAntiBlockBot\n\n"
        "Напишите: «Хочу курс» — подскажу, с чего начать."
    )
    update.message.reply_text(text, disable_web_page_preview=True)

def send_file_by_id(context, chat_id, prefix, file_id):
    section = "handouts" if prefix == "H" else "templates"
    items = _get_section_items(section)

    item = None
    for x in items:
        if str(x.get("id", "")) == str(file_id):
            item = x
            break

    if not item:
        context.bot.send_message(chat_id=chat_id, text="Файл не найден. Пересобери kb/content.json.")
        return

    relpath = item.get("relpath", "")
    p = safe_resolve_relpath(relpath)
    if not p:
        context.bot.send_message(chat_id=chat_id, text="Файл отсутствует на сервере: %s" % relpath)
        return

    title = item.get("title") or p.name
    try:
        with open(str(p), "rb") as f:
            context.bot.send_document(chat_id=chat_id, document=f, filename=p.name, caption=title)
    except Exception:
        context.bot.send_message(chat_id=chat_id, text="Не смог отправить файл. Проверь права/размер/формат.")

# -----------------------------
# Handlers
# -----------------------------
def cmd_start(update, context):
    touch_heartbeat()
    keyboard = [
        ["📎 Раздатка"],
        ["🧾 Шаблон"],
        ["📚 Курс"],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    update.message.reply_text(
        "Привет! Я AiAntiblokBot.\n\n"
        "Помогаю по теме блокировок счетов/карт, 115-ФЗ и финансовой безопасности.\n"
        "Выберите раздел ниже или опишите ситуацию текстом.",
        reply_markup=reply_markup
    )

def cmd_help(update, context):
    update.message.reply_text(
        "/start — меню\n"
        "/status — диагностика\n\n"
        "Кнопки:\n"
        "• 📎 Раздатка — материалы\n"
        "• 🧾 Шаблон — документы\n"
        "• 📚 Курс — ссылки"
    )

def cmd_status(update, context):
    touch_heartbeat()
    uid = update.effective_user.id if update.effective_user else None
    if ADMIN_IDS and uid not in ADMIN_IDS:
        update.message.reply_text("Недостаточно прав.")
        return

    pid = os.getpid()
    uptime = int(time.time()) - START_TS
    hb_age = heartbeat_age()

    kb_files = 0
    try:
        for sub in ("handouts", "templates"):
            d = KB_DIR / sub
            if d.exists():
                kb_files += len([p for p in d.rglob("*") if p.is_file()])
    except Exception:
        pass

    text = "\n".join([
        "🤖 AiAntiblokBot",
        "🆔 PID: %s" % pid,
        "⏱️ Uptime: %s" % fmt_uptime(uptime),
        "❤️ Heartbeat age: %s" % (("%ss" % hb_age) if hb_age is not None else "n/a"),
        "📚 KB files: %s" % kb_files,
        "⚙️ Mode: %s" % ("PAID" if PAID_MODE else "FREE"),
        "🐍 Python: %s" % platform.python_version(),
    ])
    update.message.reply_text(text)

def on_callback(update, context):
    touch_heartbeat()
    q = update.callback_query
    if not q:
        return
    try:
        q.answer()
    except Exception:
        pass

    data = (q.data or "").strip()
    chat_id = q.message.chat_id

    if data == "CHECK_SUB":
        uid = update.effective_user.id if update.effective_user else None
        if not uid:
            q.edit_message_text("Не вижу пользователя. Попробуйте снова.")
            return
        if is_subscriber(context.bot, uid):
            q.edit_message_text("✅ Подписка подтверждена. Можно пользоваться ботом.")
        else:
            q.edit_message_text(
                "Подписка не найдена.\n"
                "Подпишитесь: https://t.me/Borodulin_expert\n"
                "И нажмите «Проверить подписку» ещё раз."
            )
        return

    # пагинация: PAGE|H|1  или PAGE|T|0
    if data.startswith("PAGE|"):
        parts = data.split("|")
        if len(parts) == 3:
            prefix = parts[1]
            try:
                page = int(parts[2])
            except Exception:
                page = 0

            uid = update.effective_user.id if update.effective_user else None
            if uid and not is_subscriber(context.bot, uid):
                q.edit_message_text("Доступ к материалам — только подписчикам:\nhttps://t.me/Borodulin_expert")
                return

            section = "handouts" if prefix == "H" else "templates"
            kb, page, max_page = _build_list_keyboard(section, prefix, page)
            header = "📎 Раздатка (стр. %s/%s)" % (page + 1, max_page + 1) if prefix == "H" else "🧾 Шаблоны (стр. %s/%s)" % (page + 1, max_page + 1)
            if kb:
                try:
                    q.edit_message_text(header, reply_markup=kb)
                except Exception:
                    context.bot.send_message(chat_id=chat_id, text=header, reply_markup=kb)
            return

    # файл: FILE|H|<id> или FILE|T|<id>
    if data.startswith("FILE|"):
        parts = data.split("|")
        if len(parts) == 3:
            prefix = parts[1]
            file_id = parts[2]

            uid = update.effective_user.id if update.effective_user else None
            if uid and not is_subscriber(context.bot, uid):
                q.edit_message_text("Доступ к материалам — только подписчикам:\nhttps://t.me/Borodulin_expert")
                return

            send_file_by_id(context, chat_id, prefix, file_id)
            return

def handle_text(update, context):
    touch_heartbeat()
    if not update.message:
        return

    uid = update.effective_user.id if update.effective_user else None
    txt = (update.message.text or "").strip()

    # лог входящих
    try:
        uname = update.effective_user.username or ""
        logging.info("msg from %s(%s): %s", uname, uid, txt)
    except Exception:
        pass

    # 1) меню-кнопки — ОБЯЗАТЕЛЬНО ПЕРВЫМИ
    if txt in ("Раздатка", "📎 Раздатка"):
        if not gate_or_prompt(update, context):
            return
        show_list(update, "handouts", "H", page=0)
        return

    if txt in ("Шаблон", "🧾 Шаблон"):
        if not gate_or_prompt(update, context):
            return
        show_list(update, "templates", "T", page=0)
        return

    if txt in ("Курс", "📚 Курс"):
        send_courses(update, context)
        return

    # 2) быстрые фразы
    if txt.lower() in ("хочу курс", "курс хочу", "давай курс"):
        send_courses(update, context)
        return

    # 3) мат/агрессия
    if is_abusive(txt):
        update.message.reply_text(random.choice(HUMOR_VARIANTS))
        return

    # 4) смысловая маршрутизация
    if is_on_topic(txt):
        answer_on_topic(update, context)
    else:
        answer_off_topic(update, context)

def on_error(update, context):
    try:
        logging.exception("Unhandled error: %s", context.error)
    except Exception:
        pass

# -----------------------------
# main
# -----------------------------
def main():
    init_logging()
    touch_heartbeat()

    if not BOT_TOKEN:
        logging.error("BOT_TOKEN not set (env BOT_TOKEN or data/token.txt)")
        return

    updater = Updater(token=BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_error_handler(on_error)

    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("help", cmd_help))
    dp.add_handler(CommandHandler("status", cmd_status))

    dp.add_handler(CallbackQueryHandler(on_callback))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    logging.info("KB content.json exists: %s", "yes" if CONTENT_JSON.exists() else "no")
    logging.info("Bot starting polling... username check via getMe soon")

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
