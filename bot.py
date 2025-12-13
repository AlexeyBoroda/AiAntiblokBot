#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AiAntiblokBot — тематический Telegram-бот (Python 3.6+, python-telegram-bot==12.8)

MVP функции:
1) Доступ к материалам только подписчикам канала https://t.me/Borodulin_expert
2) Меню: Раздатка / Шаблон / Курс
   - Раздатка: список файлов из kb/handouts (через kb/content.json)
   - Шаблон: список файлов из kb/templates (через kb/content.json)
   - Курс: список ссылок
   - По нажатию на пункт списка — отправка файла
3) Вопросы по теме блокировок/115-ФЗ:
   - ищем релевантные фрагменты в базе знаний kb/text (индекс kb/text_index.json)
   - формируем ответ через GigaChat API (с учётом контекста из базы)
4) Вопросы не по теме: вежливый отбой (“консультирую только по блокировкам…”)
5) Мат/агрессия: юморные ответы (рандом)
6) /status — диагностика

Важно:
- Токены/ключи храним только в .env (в корне проекта рядом с bot.py)
- Для SSL к GigaChat обычно нужен CA bundle. По умолчанию: data/ca/ca_bundle.pem
"""

import os
import re
import json
import time
import math
import random
import logging
import platform
from pathlib import Path

from dotenv import load_dotenv

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    Filters,
)

from gigachat_client import GigaChatClient


# -----------------------------
# Paths / env
# -----------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
KB_DIR = BASE_DIR / "kb"

# Load .env first!
load_dotenv(str(BASE_DIR / ".env"))

CONTENT_JSON = KB_DIR / "content.json"
TEXT_INDEX_JSON = KB_DIR / "text_index.json"
HEARTBEAT_FILE = DATA_DIR / "heartbeat.txt"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@Borodulin_expert").strip()  # @channelusername

# if you want feature flags later
PAID_MODE = os.getenv("PAID_MODE", "0").strip().lower() in ("1", "true", "yes")

# /status can be limited to admins (comma-separated user ids)
ADMIN_IDS = set()
_admin_raw = os.getenv("ADMIN_IDS", "").strip()
if _admin_raw:
    try:
        ADMIN_IDS = set(int(x.strip()) for x in _admin_raw.split(",") if x.strip())
    except Exception:
        ADMIN_IDS = set()

# GigaChat config
GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "").strip()  # WITHOUT "Basic "
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat").strip()
GIGACHAT_CA_BUNDLE = os.getenv("GIGACHAT_CA_BUNDLE", str(DATA_DIR / "ca" / "ca_bundle.pem")).strip()
GIGACHAT_VERIFY = os.getenv("GIGACHAT_VERIFY", "1").strip() not in ("0", "false", "False", "no", "NO")

START_TS = int(time.time())

# membership cache
_SUB_CACHE = {}  # user_id -> (ts, bool)
SUB_CACHE_TTL = 60  # sec

# text index cache
_TEXT_INDEX = None
_TEXT_INDEX_MTIME = 0


# -----------------------------
# Logging
# -----------------------------
def init_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "bot.log"

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
    """
    content.json:
    {
      "handouts": [{"id":"...", "title":"...", "relpath":"kb/handouts/file.pdf", ...}],
      "templates": [{"id":"...", "title":"...", "relpath":"kb/templates/file.docx", ...}]
    }
    """
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


def gate_or_prompt(update, context):
    """
    True => доступ разрешён
    False => отправлено сообщение “подпишитесь”
    """
    uid = update.effective_user.id if update.effective_user else None
    if not uid:
        return False

    if is_subscriber(context.bot, uid):
        return True

    text = (
        "Доступ к материалам — только для подписчиков канала:\n"
        "✅ https://t.me/Borodulin_expert\n\n"
        "Подпишитесь и нажмите «Проверить подписку»."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Подписаться", url="https://t.me/Borodulin_expert")],
        [InlineKeyboardButton("Проверить подписку", callback_data="CHECK_SUB")],
    ])
    update.message.reply_text(text, reply_markup=kb)
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
# Topic routing
# -----------------------------
TOPIC_KEYWORDS = [
    "блок", "замороз", "115", "комплаенс", "росфин", "росфинмониторинг",
    "счет", "счёт", "карта", "перевод", "платеж", "платёж",
    "дбо", "банк", "огранич", "разблок", "попал в базу", "мошенническ",
]


def is_on_topic(text):
    t = (text or "").lower()
    return any(k in t for k in TOPIC_KEYWORDS)


def answer_off_topic(update, context):
    update.message.reply_text(
        "Я ИИ‑помощник и консультирую только по вопросам блокировок счетов/карт, 115‑ФЗ и комплаенса.\n"
        "Переформулируйте вопрос так, чтобы была связь с блокировкой."
    )


# -----------------------------
# KB text index (retrieval)
# -----------------------------
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+")


def _tokenize(s):
    return [w.lower() for w in _WORD_RE.findall(s or "") if len(w) >= 2]


def load_text_index():
    global _TEXT_INDEX, _TEXT_INDEX_MTIME
    if not TEXT_INDEX_JSON.exists():
        _TEXT_INDEX = None
        _TEXT_INDEX_MTIME = 0
        return None

    mtime = int(TEXT_INDEX_JSON.stat().st_mtime)
    if _TEXT_INDEX and _TEXT_INDEX_MTIME == mtime:
        return _TEXT_INDEX

    try:
        obj = json.loads(TEXT_INDEX_JSON.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and "chunks" in obj and "postings" in obj:
            _TEXT_INDEX = obj
            _TEXT_INDEX_MTIME = mtime
            return obj
    except Exception:
        pass

    _TEXT_INDEX = None
    _TEXT_INDEX_MTIME = mtime
    return None


def search_kb(query, top_k=3):
    """
    Simple TF-IDF scoring over chunk postings from kb/rebuild_text_index.py output.
    Returns list of chunks (dict) with fields: text, source, idx, ...
    """
    idx = load_text_index()
    if not idx:
        return []

    q_terms = _tokenize(query)
    if not q_terms:
        return []

    postings = idx.get("postings", {}) or {}
    df = idx.get("df", {}) or {}
    N = int(idx.get("N", 0) or 0)
    chunks = idx.get("chunks", []) or []

    scores = {}
    for term in q_terms:
        plist = postings.get(term)
        if not plist:
            continue
        dfi = int(df.get(term, 0) or 0)
        # idf with smoothing
        idf = math.log((N + 1.0) / (dfi + 1.0)) + 1.0
        for item in plist:
            # item: [chunk_idx, tf]
            try:
                cidx = int(item[0])
                tf = float(item[1])
            except Exception:
                continue
            scores[cidx] = scores.get(cidx, 0.0) + tf * idf

    if not scores:
        return []

    best = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    out = []
    for cidx, sc in best:
        if 0 <= cidx < len(chunks):
            ch = chunks[cidx]
            ch2 = dict(ch)
            ch2["_score"] = sc
            out.append(ch2)
    return out


# -----------------------------
# GigaChat answering
# -----------------------------
def build_system_prompt():
    return (
        "Ты — консультант по блокировкам банковских счетов/карт, 115‑ФЗ, комплаенсу и финансовой безопасности бизнеса.\n"
        "Отвечай по‑деловому, кратко, по шагам.\n"
        "Если пользователь спрашивает не по теме блокировок/комплаенса — вежливо откажи и попроси переформулировать.\n"
        "Не выдумывай факты. Если данных недостаточно — задай 2–3 уточняющих вопроса.\n"
    )


def build_user_prompt(user_text, kb_chunks):
    ctx = ""
    if kb_chunks:
        parts = []
        for ch in kb_chunks:
            src = ch.get("source", "kb")
            txt = (ch.get("text") or "").strip()
            if not txt:
                continue
            # safety: limit context length
            if len(txt) > 1200:
                txt = txt[:1200] + "…"
            parts.append("Источник: {}\n{}".format(src, txt))
        if parts:
            ctx = "Ниже выдержки из базы знаний. Используй их как основу ответа.\n\n" + "\n\n---\n\n".join(parts) + "\n\n"

    return ctx + "Вопрос пользователя:\n" + user_text


def answer_on_topic(update, context):
    user_text = (update.message.text or "").strip()

    kb_chunks = search_kb(user_text, top_k=3)

    # If we have no KB at all, we can still answer via GigaChat, but keep it safe.
    if not GIGACHAT_AUTH_KEY:
        # fallback: no gigachat configured
        update.message.reply_text(
            "Сейчас ИИ‑модуль не настроен.\n"
            "Но я могу подсказать базовый план: что именно заблокировали (счёт/карта), какая операция, кто контрагент и что ответил банк?"
        )
        return

    try:
        client = GigaChatClient(
            auth_key=GIGACHAT_AUTH_KEY,
            scope=GIGACHAT_SCOPE,
            model=GIGACHAT_MODEL,
            ca_bundle_path=GIGACHAT_CA_BUNDLE,
            verify=GIGACHAT_VERIFY,
            timeout=30,
        )
        resp = client.chat(
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(user_text, kb_chunks),
            temperature=0.2,
            max_tokens=900,
        )
        update.message.reply_text(resp)
    except Exception as e:
        logging.exception("GigaChat error: %s", e)
        update.message.reply_text("Сейчас не могу обратиться к ИИ‑модулю. Попробуйте ещё раз позже.")


# -----------------------------
# UI: lists / files / courses
# -----------------------------
def show_list(update, section_key, prefix):
    """
    section_key: "handouts" | "templates"
    prefix: "H" | "T"
    """
    content = load_content()
    items = content.get(section_key, []) or []
    if not items:
        update.message.reply_text(
            "Пока пусто.\n"
            "Добавьте файлы в kb/{}/ и запустите kb/rebuild_content.py (пересоберёт kb/content.json).".format(
                "handouts" if section_key == "handouts" else "templates"
            )
        )
        return

    # inline buttons (max 60)
    rows = []
    for it in items[:60]:
        title = it.get("title") or it.get("filename") or it.get("id") or "Файл"
        cb = "{}:{}".format(prefix, it.get("id", ""))
        rows.append([InlineKeyboardButton(title, callback_data=cb)])

    update.message.reply_text("Выберите файл:", reply_markup=InlineKeyboardMarkup(rows))


def send_courses(update, context):
    text = (
        "📚 Курсы:\n"
        "1) «Как вести бизнес, чтобы не заблокировали счета» — https://stepik.org/a/252040\n"
        "2) Лид‑магнит/бот — https://t.me/BorodulinAntiBlockBot\n\n"
        "Напишите: «Хочу курс» — подскажу, с чего начать."
    )
    update.message.reply_text(text, disable_web_page_preview=True)


def send_file_by_id(context, chat_id, prefix, file_id, message_to_edit=None):
    content = load_content()
    if prefix == "H":
        items = content.get("handouts", []) or []
    elif prefix == "T":
        items = content.get("templates", []) or []
    else:
        if message_to_edit:
            message_to_edit.edit_text("Неизвестный тип файла.")
        return

    item = None
    for x in items:
        if str(x.get("id", "")) == str(file_id):
            item = x
            break

    if not item:
        if message_to_edit:
            message_to_edit.edit_text("Файл не найден. Пересоберите kb/content.json.")
        return

    relpath = item.get("relpath", "")
    p = safe_resolve_relpath(relpath)
    if not p:
        if message_to_edit:
            message_to_edit.edit_text("Файл отсутствует на сервере: {}".format(relpath))
        return

    title = item.get("title") or p.name
    try:
        with open(str(p), "rb") as f:
            context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=p.name,
                caption=title
            )
    except Exception:
        if message_to_edit:
            message_to_edit.edit_text("Не смог отправить файл. Проверьте права/размер/формат.")


# -----------------------------
# Handlers
# -----------------------------
def cmd_start(update, context):
    touch_heartbeat()
    keyboard = [
        ["Раздатка"],
        ["Шаблон"],
        ["Курс"],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    update.message.reply_text(
        "Привет! Я AiAntiblokBot.\n\n"
        "Я помогаю по теме блокировок счетов/карт, 115‑ФЗ и комплаенса.\n"
        "Выберите раздел ниже или опишите ситуацию текстом.",
        reply_markup=reply_markup
    )


def cmd_help(update, context):
    update.message.reply_text(
        "/start — меню\n"
        "/status — диагностика\n\n"
        "Кнопки:\n"
        "• Раздатка — материалы\n"
        "• Шаблон — документы\n"
        "• Курс — ссылки"
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
        for sub in ("handouts", "templates", "text", "files"):
            d = KB_DIR / sub
            if d.exists():
                kb_files += len([p for p in d.rglob("*") if p.is_file()])
    except Exception:
        pass

    # check gigachat config
    gc_ok = "yes" if GIGACHAT_AUTH_KEY else "no"
    ca_ok = "yes" if (GIGACHAT_CA_BUNDLE and Path(GIGACHAT_CA_BUNDLE).exists()) else "no"
    idx_ok = "yes" if TEXT_INDEX_JSON.exists() else "no"
    content_ok = "yes" if CONTENT_JSON.exists() else "no"

    text = "\n".join([
        "🤖 AiAntiblokBot",
        "🆔 PID: {}".format(pid),
        "⏱️ Uptime: {}".format(fmt_uptime(uptime)),
        "❤️ Heartbeat age: {}".format("%ss" % hb_age if hb_age is not None else "n/a"),
        "📚 KB files: {}".format(kb_files),
        "📦 content.json: {}".format(content_ok),
        "📇 text_index.json: {}".format(idx_ok),
        "⚙️ Mode: {}".format("PAID" if PAID_MODE else "FREE"),
        "🧠 GigaChat configured: {}".format(gc_ok),
        "🔒 CA bundle present: {}".format(ca_ok),
        "🐍 Python: {}".format(platform.python_version()),
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

    # file: H:<id> or T:<id>
    if ":" in data:
        prefix, file_id = data.split(":", 1)
        if prefix in ("H", "T"):
            uid = update.effective_user.id if update.effective_user else None
            if uid and not is_subscriber(context.bot, uid):
                q.edit_message_text("Доступ к материалам — только подписчикам:\nhttps://t.me/Borodulin_expert")
                return
            send_file_by_id(context, chat_id, prefix, file_id, message_to_edit=q.message)
            return


def handle_text(update, context):
    touch_heartbeat()

    if not update.message:
        return

    uid = update.effective_user.id if update.effective_user else None
    txt = (update.message.text or "").strip()

    try:
        uname = update.effective_user.username or ""
        logging.info("msg from %s(%s): %s", uname, uid, txt)
    except Exception:
        pass

    # 1) menu buttons
    if txt in ("Раздатка", "📎 Раздатка"):
        if not gate_or_prompt(update, context):
            return
        show_list(update, "handouts", "H")
        return

    if txt in ("Шаблон", "🧾 Шаблон"):
        if not gate_or_prompt(update, context):
            return
        show_list(update, "templates", "T")
        return

    if txt in ("Курс", "📚 Курс"):
        send_courses(update, context)
        return

    # 2) quick phrases
    if txt.lower() in ("хочу курс", "курс хочу", "давай курс"):
        send_courses(update, context)
        return

    # 3) abusive -> humor
    if is_abusive(txt):
        update.message.reply_text(random.choice(HUMOR_VARIANTS))
        return

    # 4) topic routing
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
        logging.error("BOT_TOKEN is empty. Put it into .env as BOT_TOKEN=... and restart.")
        return

    updater = Updater(token=BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_error_handler(on_error)

    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("help", cmd_help))
    dp.add_handler(CommandHandler("status", cmd_status))

    dp.add_handler(CallbackQueryHandler(on_callback))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    logging.info("content.json exists: %s", "yes" if CONTENT_JSON.exists() else "no")
    logging.info("text_index.json exists: %s", "yes" if TEXT_INDEX_JSON.exists() else "no")
    logging.info("GigaChat configured: %s scope=%s model=%s verify=%s ca=%s",
                 "yes" if GIGACHAT_AUTH_KEY else "no",
                 GIGACHAT_SCOPE, GIGACHAT_MODEL, str(GIGACHAT_VERIFY),
                 GIGACHAT_CA_BUNDLE)

    logging.info("Bot starting polling...")

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
