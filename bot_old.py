#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import uuid
import random
import logging
import platform
from pathlib import Path

import requests
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

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
KB_DIR = BASE_DIR / "kb"

CONTENT_JSON = KB_DIR / "content.json"
KB_TEXT_DIR = KB_DIR / "text"

HEARTBEAT_FILE = DATA_DIR / "heartbeat.txt"
CA_BUNDLE = DATA_DIR / "ca" / "ca_bundle.pem"

load_dotenv(str(BASE_DIR / ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@Borodulin_expert").strip()

ADMIN_IDS = set()
_admin_raw = os.getenv("ADMIN_IDS", "").strip()
if _admin_raw:
    try:
        ADMIN_IDS = set(int(x.strip()) for x in _admin_raw.split(",") if x.strip())
    except Exception:
        ADMIN_IDS = set()

START_TS = int(time.time())

_SUB_CACHE = {}
SUB_CACHE_TTL = 60

GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "").strip()
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat:latest").strip()

GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

_GIGA_TOKEN_CACHE = {"ts": 0, "token": ""}

CASE_STATE_FILE = DATA_DIR / "case_state.json"
CASE_TTL = 60 * 60 * 6

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
# Content loading (handouts/templates/courses)
# -----------------------------
def load_content():
    try:
        raw = CONTENT_JSON.read_text(encoding="utf-8")
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return {"handouts": [], "templates": [], "courses": []}
        obj.setdefault("handouts", [])
        obj.setdefault("templates", [])
        obj.setdefault("courses", [])
        return obj
    except Exception:
        return {"handouts": [], "templates": [], "courses": []}


def safe_resolve_relpath(relpath):
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
# Moderation / humor
# -----------------------------
HUMOR_VARIANTS = [
    "Ок, без грубостей 🙂 Давайте по делу: что именно заблокировали и что написал банк?",
    "Понял эмоции. Давайте быстро разрулим: счет/карта/ДБО? и есть ли уведомление от банка?",
    "Договорились 🙂 Сначала факты: дата блокировки и причина в уведомлении.",
]

_BAD_WORDS = ["сука", "бляд", "хуй", "пизд", "еба", "ёба", "нахуй", "мудак", "говно", "идиот", "тупишь"]

def is_abusive(text):
    t = (text or "").lower()
    return any(w in t for w in _BAD_WORDS)


# -----------------------------
# Intents
# -----------------------------
GREET_RE = re.compile(r"^\s*(привет|здравствуй|здравствуйте|добрый\s*(день|вечер|утро)|хай|hello|hi)\s*[!.]*\s*$", re.I)

TERM_Q_RE = re.compile(
    r"(что\s+такое|что\s+значит|расшифруй|расшифровка|аббревиатура|термин)\s+([A-Za-zА-Яа-яЁё0-9\-_/]{2,30})",
    re.I
)

TOPIC_KEYWORDS = [
    "блок", "замороз", "огранич", "115", "пфтк", "под/фт", "росфин", "счет", "счёт",
    "карта", "перевод", "платеж", "платёж", "дбо", "банк", "комплаенс",
    "подозр", "подозрительные", "сомнитель", "зск", "знай своего клиента",
    "красная зона", "желтая зона", "жёлтая зона", "зелёная зона", "зеленая зона",
]

def is_on_topic(text):
    t = (text or "").lower()
    return any(k in t for k in TOPIC_KEYWORDS)

def is_greeting(text):
    return bool(GREET_RE.match(text or ""))

def extract_term_query(text):
    m = TERM_Q_RE.search(text or "")
    if not m:
        return None
    return m.group(2).strip()


# -----------------------------
# Case-state persistence
# -----------------------------
def load_case_state():
    try:
        if not CASE_STATE_FILE.exists():
            return {}
        obj = json.loads(CASE_STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return {}
        return obj
    except Exception:
        return {}

def save_case_state(state):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CASE_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def get_user_case(state, user_id):
    rec = state.get(str(user_id))
    if not rec:
        return None
    ts = rec.get("ts", 0)
    if int(time.time()) - int(ts) > CASE_TTL:
        return None
    return rec

def set_user_case(state, user_id, rec):
    rec["ts"] = int(time.time())
    state[str(user_id)] = rec
    save_case_state(state)

def clear_user_case(state, user_id):
    if str(user_id) in state:
        del state[str(user_id)]
        save_case_state(state)


# -----------------------------
# KB search
# -----------------------------
def strip_markdown(s):
    if not s:
        return ""
    s = re.sub(r"```.*?```", "", s, flags=re.S)
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"^\s*[-*+]\s+", "", s, flags=re.M)
    s = s.replace("**", "").replace("*", "").replace("_", "")
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()

def tokenize(text):
    t = (text or "").lower()
    t = re.sub(r"[^a-zа-яё0-9\s]", " ", t)
    parts = [p for p in t.split() if len(p) >= 2]
    return parts[:50]

def kb_search(query, max_docs=3, max_chars=2600):
    if not KB_TEXT_DIR.exists():
        return []

    q_tokens = tokenize(query)
    if not q_tokens:
        return []

    scored = []
    for p in KB_TEXT_DIR.glob("*.md"):
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        low = txt.lower()
        score = 0
        for tok in q_tokens:
            if tok in low:
                score += 1
        if score <= 0:
            continue
        scored.append((score, txt))

    scored.sort(key=lambda x: x[0], reverse=True)

    snippets = []
    total = 0
    for score, doc in scored[:max_docs]:
        clean = strip_markdown(doc)
        if not clean:
            continue
        piece = clean[:950]
        if piece and piece not in snippets:
            if total + len(piece) > max_chars:
                break
            snippets.append(piece)
            total += len(piece)

    return snippets


# -----------------------------
# Pretty formatting for Telegram (no markdown)
# -----------------------------
def normalize_text(s):
    s = (s or "").strip()
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # убрать случайный markdown
    s = strip_markdown(s)
    return s

def split_sentences(text):
    # грубый разбор на предложения
    t = (text or "").strip()
    if not t:
        return []
    parts = re.split(r"(?<=[.!?])\s+", t)
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    return out

def make_pretty_answer(raw):
    """
    Превращает "простыню" в читабельный ответ:
    - короткое резюме
    - 3-6 шагов
    - что приложить
    - 1 уточняющий вопрос
    """
    t = normalize_text(raw)
    if not t:
        return ""

    # Если модель уже дала эмодзи — не ломаем сильно, только нормализуем переносы
    if "✅" in t or "1️⃣" in t or "⚠️" in t:
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

    sents = split_sentences(t)

    # Резюме: первые 1-2 предложения
    summary = " ".join(sents[:2]).strip()
    rest = " ".join(sents[2:]).strip()

    # попытка вытащить шаги: берём следующие 4-6 смысловых кусочков
    step_sents = split_sentences(rest)[:6] if rest else []
    steps = []
    for i, s in enumerate(step_sents[:6], 1):
        s = s.strip()
        if not s:
            continue
        steps.append("%s %s" % (["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"][i-1], s))

    # что приложить (ключевые слова)
    attach = []
    low = t.lower()
    if any(k in low for k in ["договор", "контракт"]):
        attach.append("✅ договор/контракт + спецификация/счет")
    if any(k in low for k in ["акт", "упд"]):
        attach.append("✅ акт/УПД/накладные")
    if any(k in low for k in ["платеж", "платёж", "платежка", "платёжка"]):
        attach.append("✅ платежки/выписка по счету")
    if any(k in low for k in ["переписк", "чат", "почт"]):
        attach.append("✅ переписка с контрагентом (email/чат)")
    if any(k in low for k in ["кп", "коммерческ"]):
        attach.append("✅ КП/заказ/техзадание (если было)")

    # уточняющий вопрос — если нет явного вопроса в тексте
    ask = "❓ Уточните: что банк написал в уведомлении (1–2 фразы) и какая операция вызвала стоп?"
    if "?" in t:
        # если уже есть вопросы — не добавляем второй
        ask = ""

    out = []
    if summary:
        out.append("🧩 Коротко: " + summary)
    if steps:
        out.append("🛠 Что делать сейчас:\n" + "\n".join(steps))
    if attach:
        out.append("📎 Что обычно прикладывают:\n" + "\n".join(attach))
    if ask:
        out.append(ask)

    result = "\n\n".join(out).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


# -----------------------------
# GigaChat client
# -----------------------------
def gigachat_get_token():
    now = int(time.time())
    if _GIGA_TOKEN_CACHE["token"] and (now - _GIGA_TOKEN_CACHE["ts"] < 25 * 60):
        return _GIGA_TOKEN_CACHE["token"]

    if not GIGACHAT_AUTH_KEY:
        return ""

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": "Basic " + GIGACHAT_AUTH_KEY,
    }

    verify = str(CA_BUNDLE) if CA_BUNDLE.exists() else True
    try:
        r = requests.post(GIGACHAT_OAUTH_URL, headers=headers, data={"scope": GIGACHAT_SCOPE}, timeout=30, verify=verify)
        if r.status_code != 200:
            logging.error("GigaChat oauth bad status=%s body=%s", r.status_code, r.text[:400])
            return ""
        j = r.json()
        token = j.get("access_token", "")
        if token:
            _GIGA_TOKEN_CACHE["token"] = token
            _GIGA_TOKEN_CACHE["ts"] = now
        return token
    except Exception as e:
        logging.exception("GigaChat oauth error: %s", e)
        return ""


SYSTEM_PROMPT = (
    "Ты AI-помощник по блокировкам счетов/карт, 115-ФЗ, ЗСК и комплаенсу.\n"
    "Требования к стилю ответа:\n"
    "- Пиши на русском.\n"
    "- Коротко и структурно.\n"
    "- Используй эмодзи-маркеры: 🧩 🛠 ✅ ⚠️ ❓ 1️⃣ 2️⃣ 3️⃣.\n"
    "- НЕ используй markdown-символы: # * _ ```.\n"
    "- Делай переносы строк и короткие абзацы (чтобы читалось в Telegram).\n"
    "- Не упоминай названия файлов.\n"
)

def gigachat_answer(user_prompt, context_snippets):
    token = gigachat_get_token()
    if not token:
        return ""

    verify = str(CA_BUNDLE) if CA_BUNDLE.exists() else True
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    }

    kb_block = ""
    if context_snippets:
        kb_block = "\n\n".join(["Фрагмент базы знаний:\n" + s for s in context_snippets])

    # каркас ответа — чтобы почти всегда выходило красиво
    prompt = (
        SYSTEM_PROMPT
        + "\n\n"
        + (kb_block + "\n\n" if kb_block else "")
        + "Сформируй ответ строго по шаблону:\n"
          "🧩 Коротко: 1-2 предложения.\n"
          "🛠 Что делать сейчас: 3-5 пунктов с 1️⃣ 2️⃣ 3️⃣.\n"
          "📎 Что приложить: 3-6 пунктов с ✅.\n"
          "❓ Уточняющий вопрос: 1 вопрос.\n\n"
        + "Вопрос пользователя:\n"
        + user_prompt.strip()
    )

    payload = {
        "model": GIGACHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 900,
    }

    try:
        r = requests.post(GIGACHAT_CHAT_URL, headers=headers, json=payload, timeout=45, verify=verify)
        if r.status_code != 200:
            logging.error("GigaChat chat bad status=%s body=%s", r.status_code, r.text[:400])
            return ""
        j = r.json()
        choices = j.get("choices") or []
        if not choices:
            return ""
        content = choices[0].get("message", {}).get("content", "") or ""
        return make_pretty_answer(content)
    except Exception as e:
        logging.exception("GigaChat chat error: %s", e)
        return ""


# -----------------------------
# UI: lists / files / courses
# -----------------------------
def show_list(update, section, prefix):
    content = load_content()
    items = content.get(section, []) or []
    if not items:
        update.message.reply_text("Пока пусто.")
        return

    rows = []
    for it in items[:60]:
        title = it.get("title") or it.get("filename") or it.get("id") or "Файл"
        rows.append([InlineKeyboardButton(title, callback_data="{}:{}".format(prefix, it.get("id", "")))])
    update.message.reply_text("Выберите файл:", reply_markup=InlineKeyboardMarkup(rows))


def send_courses(update):
    content = load_content()
    courses = content.get("courses", []) or []
    if courses:
        lines = ["📚 Курсы:"]
        for i, c in enumerate(courses[:10], 1):
            t = c.get("title") or "Курс"
            u = c.get("url") or ""
            if u:
                lines.append("%s) %s — %s" % (i, t, u))
            else:
                lines.append("%s) %s" % (i, t))
        lines.append("\nНапишите: «Хочу курс» — подскажу, с чего начать.")
        update.message.reply_text("\n".join(lines), disable_web_page_preview=True)
    else:
        update.message.reply_text(
            "📚 Курс «Как вести бизнес, чтобы не блокировали счета».\n"
            "Напишите: Хочу курс",
            disable_web_page_preview=True
        )


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
            message_to_edit.edit_text("Файл отсутствует на сервере.")
        return

    title = item.get("title") or p.name
    try:
        context.bot.send_document(
            chat_id=chat_id,
            document=open(str(p), "rb"),
            filename=p.name,
            caption=title
        )
    except Exception:
        if message_to_edit:
            message_to_edit.edit_text("Не смог отправить файл. Проверь права/размер/формат.")


# -----------------------------
# Texts
# -----------------------------
def greet_text():
    return (
        "Доброго времени суток! 👋\n\n"
        "Я помогу по блокировкам счетов/карт, 115-ФЗ, ЗСК и комплаенсу.\n\n"
        "Напишите одним сообщением:\n"
        "1️⃣ что заблокировали (счёт/карта/ДБО)\n"
        "2️⃣ дата\n"
        "3️⃣ что написал банк (1–2 фразы)\n"
        "4️⃣ какая операция/контрагент (если есть)"
    )

def soft_offtopic_text():
    return (
        "Я консультирую по блокировкам счетов/карт, 115-ФЗ, ЗСК и комплаенсу.\n"
        "Если хотите — могу объяснить термины/сокращения из этой области.\n\n"
        "Чтобы помочь по кейсу: напишите что заблокировали + что написал банк."
    )


# -----------------------------
# Handlers
# -----------------------------
def cmd_start(update, context):
    touch_heartbeat()
    keyboard = [
        ["📎 Раздатка", "🧾 Шаблоны"],
        ["📚 Курсы"],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    update.message.reply_text(greet_text(), reply_markup=reply_markup)


def cmd_help(update, context):
    update.message.reply_text(
        "/start — меню\n"
        "/status — диагностика\n\n"
        "Кнопки:\n"
        "• 📎 Раздатка — материалы\n"
        "• 🧾 Шаблоны — документы\n"
        "• 📚 Курсы — ссылки"
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
        for sub in ("handouts", "templates", "text"):
            d = KB_DIR / sub
            if d.exists():
                kb_files += len([p for p in d.rglob("*") if p.is_file()])
    except Exception:
        pass

    text = "\n".join([
        "🤖 AiAntiblokBot",
        "🆔 PID: {}".format(pid),
        "⏱️ Uptime: {}".format(fmt_uptime(uptime)),
        "❤️ Heartbeat age: {}".format("%ss" % hb_age if hb_age is not None else "n/a"),
        "📚 KB files: {}".format(kb_files),
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

    # greetings
    if is_greeting(txt):
        update.message.reply_text(greet_text())
        return

    # menu
    if txt in ("Раздатка", "📎 Раздатка"):
        if not gate_or_prompt(update, context):
            return
        show_list(update, "handouts", "H")
        return

    if txt in ("Шаблон", "Шаблоны", "🧾 Шаблон", "🧾 Шаблоны"):
        if not gate_or_prompt(update, context):
            return
        show_list(update, "templates", "T")
        return

    if txt in ("Курс", "Курсы", "📚 Курс", "📚 Курсы"):
        send_courses(update)
        return

    if txt.lower() in ("хочу курс", "курс хочу", "давай курс"):
        send_courses(update)
        return

    # abuse
    if is_abusive(txt):
        update.message.reply_text(random.choice(HUMOR_VARIANTS))
        return

    # state
    state = load_case_state()
    case = get_user_case(state, uid) if uid else None

    # term question -> KB -> Giga
    term = extract_term_query(txt)
    if term:
        snippets = kb_search(txt, max_docs=3)
        ans = gigachat_answer(txt, snippets)
        if ans:
            update.message.reply_text(ans)
            return
        if snippets:
            update.message.reply_text(make_pretty_answer(snippets[0]))
            return
        update.message.reply_text(
            "Пока не нашёл термин «%s» в базе.\n"
            "Если это из темы блокировок/115-ФЗ/комплаенса — уточните контекст." % term
        )
        return

    # case path
    if is_on_topic(txt) or (case is not None):
        if case is None:
            case = {"step": 1, "asked": []}

        def ask_once(question, key):
            asked = set(case.get("asked") or [])
            if key in asked:
                return False
            asked.add(key)
            case["asked"] = list(asked)
            update.message.reply_text(question)
            return True

        if case.get("step", 1) == 1:
            if re.search(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b", txt) or "вчера" in txt.lower():
                case["step"] = 2
            else:
                if ask_once("Когда заблокировали (сегодня/вчера/дата) и что именно: счёт/карта/ДБО?", "when_what"):
                    set_user_case(state, uid, case)
                    return
                case["step"] = 2

        if case.get("step", 2) == 2:
            if "подозр" in txt.lower() or "115" in txt.lower() or "зск" in txt.lower() or "красн" in txt.lower():
                case["step"] = 3
            else:
                if ask_once("Что банк указал как причину (1–2 фразы из уведомления)?", "bank_reason"):
                    set_user_case(state, uid, case)
                    return
                case["step"] = 3

        snippets = kb_search(txt, max_docs=3)
        ans = gigachat_answer(txt, snippets)
        if ans:
            update.message.reply_text(ans)
            set_user_case(state, uid, case)
            return

        # fallback
        update.message.reply_text(
            "🧩 Коротко: похоже на типовой кейс 115-ФЗ.\n\n"
            "🛠 Что нужно от вас:\n"
            "1️⃣ 1–2 фразы из уведомления банка\n"
            "2️⃣ какая операция/контрагент\n\n"
            "❓ Уточните: банк ограничил только один платеж или весь счёт/ДБО?"
        )
        set_user_case(state, uid, case)
        return

    # general KB
    snippets = kb_search(txt, max_docs=2)
    if snippets:
        ans = gigachat_answer(txt, snippets)
        if ans:
            update.message.reply_text(ans)
        else:
            update.message.reply_text(make_pretty_answer(snippets[0]))
        return

    update.message.reply_text(soft_offtopic_text())


def on_error(update, context):
    try:
        logging.exception("Unhandled error: %s", context.error)
    except Exception:
        pass


def main():
    init_logging()
    touch_heartbeat()

    if not BOT_TOKEN:
        logging.error("BOT_TOKEN is empty. Check .env")
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
    logging.info("Bot starting polling...")

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
