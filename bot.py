# -*- coding: utf-8 -*-
"""
AiAntiblokBot (python-telegram-bot==12.8, Python 3.6)

Обновлённая версия согласно ТЗ:
- ✅ Сохранение состояния кейса в data/state.json
- ✅ Структура веток (115-ФЗ/ЗСК/161-ФЗ/налоги/приставы/без объяснений)
- ✅ Анти-зацикливание с отслеживанием заданных вопросов
- ✅ RAG: 3-6 фрагментов с релевантностью
- ✅ GigaChat только для свободных вопросов (не для детерминированных частей)
- ✅ Сбор обратной связи ⭐1-⭐6 (⭐6 платная) + комментарий
- ✅ Сохранение в dialogs.jsonl и feedback.jsonl с полными метаданными
- ✅ Админ-команды /inbox и /reply с отправкой в личку (приоритет)
- ✅ Формат ответов: структурированные с эмодзи, без markdown
- ✅ Обработка оффтопа
"""

from __future__ import print_function

import os
import re
import json
import time
import uuid
import math
import hashlib
import logging
from datetime import datetime
from collections import defaultdict

import requests
from dotenv import load_dotenv

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackQueryHandler, CallbackContext
)

# -----------------------------
# Config / Paths
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
KB_DIR = os.path.join(BASE_DIR, "kb")
KB_TEXT_DIR = os.path.join(KB_DIR, "text")

CONTENT_JSON_CANDIDATES = [
    os.path.join(DATA_DIR, "content.json"),
    os.path.join(KB_DIR, "content.json"),
]

KB_INDEX_PATH = os.path.join(DATA_DIR, "kb_index.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
FEEDBACK_LOG = os.path.join(DATA_DIR, "feedback.jsonl")
DIALOGS_LOG = os.path.join(DATA_DIR, "dialogs.jsonl")

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Админ (из .env или по умолчанию)
ADMIN_IDS = []

# -----------------------------
# Logging
# -----------------------------
logger = logging.getLogger("AiAntiblokBot")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s %(levelname)s AiAntiblokBot: %(message)s")

_fh = logging.FileHandler(os.path.join(LOG_DIR, "bot.log"), encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)

_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
logger.addHandler(_sh)

# -----------------------------
# Helpers
# -----------------------------
def now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def now_ts():
    return int(time.time())

def safe_write_jsonl(path, event):
    try:
        line = json.dumps(event, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.error("Failed to write to %s: %s", path, e)

def normalize_text(s):
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def is_greeting(text):
    t = normalize_text(text)
    return t in ("привет", "здравствуй", "здравствуйте", "добрый день", "добрый вечер", "доброе утро", "хай", "hello", "hi")

def is_offtopic(text):
    """Определяет оффтоп (погода, время, общие вопросы не по теме)."""
    t = normalize_text(text)
    offtopics = ["погода", "время", "как дела", "что делаешь", "кто ты", "что ты умеешь"]
    return any(ot in t for ot in offtopics)

def make_main_keyboard():
    kb = [["📎 Раздатка", "🧾 Шаблоны", "📚 Курсы"]]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def clean_kb_markdown(text):
    """Убирает markdown из KB фрагментов."""
    if not text:
        return ""
    # remove fenced code
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    # drop headings markers
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    # bullet markers -> "• "
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.M)
    # bold/italic markers
    text = text.replace("**", "").replace("*", "").replace("__", "").replace("_", "")
    # excessive spaces
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text

def prettify_answer(text):
    """Форматирует ответ: структурированно, с эмодзи, без markdown."""
    text = clean_kb_markdown(text)
    text = text.strip()
    
    # Если длинный абзац — разбиваем на части
    if len(text) > 900:
        parts = re.split(r"(?<=[.!?])\s+", text)
        out, buf = [], ""
        for p in parts:
            if len(buf) + len(p) + 1 < 420:
                buf = (buf + " " + p).strip()
            else:
                if buf:
                    out.append(buf)
                buf = p
        if buf:
            out.append(buf)
        text = "\n\n".join(out[:6]).strip()
    
    return text

def build_feedback_keyboard(answer_id):
    """Клавиатура для оценки ⭐1-⭐6 + комментарий."""
    row1 = [
        InlineKeyboardButton("⭐1", callback_data="FB:STAR:1:%s" % answer_id),
        InlineKeyboardButton("⭐2", callback_data="FB:STAR:2:%s" % answer_id),
        InlineKeyboardButton("⭐3", callback_data="FB:STAR:3:%s" % answer_id),
        InlineKeyboardButton("⭐4", callback_data="FB:STAR:4:%s" % answer_id),
        InlineKeyboardButton("⭐5", callback_data="FB:STAR:5:%s" % answer_id),
    ]
    row2 = [
        InlineKeyboardButton("⭐6 PRO", callback_data="FB:STAR:6:%s" % answer_id),
        InlineKeyboardButton("💬 Комментарий", callback_data="FB:COMMENT:%s" % answer_id),
    ]
    return InlineKeyboardMarkup([row1, row2])

# -----------------------------
# State management (persistent to file)
# -----------------------------
STATE_LOCK = False

def load_state():
    """Загружает состояние из файла."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state_dict):
    """Сохраняет состояние в файл."""
    global STATE_LOCK
    if STATE_LOCK:
        return
    try:
        STATE_LOCK = True
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Failed to save state: %s", e)
    finally:
        STATE_LOCK = False

def get_user_state_persistent(user_id):
    """Получает состояние пользователя из файла."""
    state_dict = load_state()
    user_key = str(user_id)
    if user_key not in state_dict:
        state_dict[user_key] = {
            "branch": None,  # 115fz, zsk, 161fz, tax, bailiffs, no_reason
            "case_data": {},  # собранные ответы
            "asked_questions": [],  # список ID заданных вопросов
            "last_bot_question_id": None,
            "last_user_message_ts": None,
            "dm_available": False,  # проверка возможности писать в личку
            "last_chat_id": None,
            "thread_id": None,  # для связи ответов
        }
        save_state(state_dict)
    return state_dict[user_key], state_dict

def update_user_state_persistent(user_id, updates):
    """Обновляет состояние пользователя в файле."""
    user_state, state_dict = get_user_state_persistent(user_id)
    user_state.update(updates)
    state_dict[str(user_id)] = user_state
    save_state(state_dict)

# -----------------------------
# Content menu (content.json)
# -----------------------------
def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def load_content():
    """Загружает content.json для меню."""
    data = None
    for p in CONTENT_JSON_CANDIDATES:
        if os.path.exists(p):
            data = _load_json(p)
            if data:
                logger.info("Loaded content.json: %s", p)
                break
    if not isinstance(data, dict):
        data = {}
    
    out = {"handouts": [], "templates": [], "courses": []}
    for k in data.keys():
        lk = k.lower()
        if "раздат" in lk or "handout" in lk or "materials" in lk:
            out["handouts"] = data[k] or []
        elif "шаблон" in lk or "template" in lk:
            out["templates"] = data[k] or []
        elif "курс" in lk or "course" in lk:
            out["courses"] = data[k] or []
    
    if "handouts" in data and not out["handouts"]:
        out["handouts"] = data.get("handouts") or []
    if "templates" in data and not out["templates"]:
        out["templates"] = data.get("templates") or []
    if "courses" in data and not out["courses"]:
        out["courses"] = data.get("courses") or []
    
    return out

def _format_items(items, max_n=10):
    """Форматирует список элементов меню."""
    if not items:
        return "Пока нет материалов в списке."
    lines = []
    for i, it in enumerate(items[:max_n], 1):
        if isinstance(it, str):
            lines.append("%d) %s" % (i, it))
            continue
        if isinstance(it, dict):
            title = (it.get("title") or it.get("name") or "Материал").strip()
            url = (it.get("url") or it.get("link") or "").strip()
            relpath = (it.get("relpath") or "").strip()
            if url:
                lines.append("%d) %s — %s" % (i, title, url))
            elif relpath and os.path.exists(os.path.join(BASE_DIR, relpath)):
                # можно отправить файл
                lines.append("%d) %s" % (i, title))
            else:
                lines.append("%d) %s" % (i, title))
    return "\n".join(lines).strip()

# -----------------------------
# KB indexing (RAG)
# -----------------------------
WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+")

def tokenize(text):
    text = (text or "").lower()
    return [w for w in WORD_RE.findall(text) if len(w) >= 2]

def load_kb_documents():
    docs = []
    if os.path.isdir(KB_TEXT_DIR):
        for fn in os.listdir(KB_TEXT_DIR):
            if fn.lower().endswith(".md"):
                path = os.path.join(KB_TEXT_DIR, fn)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        txt = f.read()
                    docs.append({"id": "text/%s" % fn, "text": txt})
                except Exception:
                    continue
    if os.path.isdir(KB_DIR):
        for fn in os.listdir(KB_DIR):
            if fn.lower().endswith(".md") and fn not in ["README.md", "readme.md"]:
                path = os.path.join(KB_DIR, fn)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        txt = f.read()
                    docs.append({"id": fn, "text": txt})
                except Exception:
                    continue
    return docs

def rebuild_kb_index():
    docs = load_kb_documents()
    if not docs:
        logger.info("KB docs not found. Index not rebuilt.")
        return {"docs": [], "df": {}, "doc_len": {}}
    
    df = {}
    doc_len = {}
    index_docs = []
    
    for d in docs:
        tokens = tokenize(d["text"])
        doc_len[d["id"]] = len(tokens)
        seen = set(tokens)
        for t in seen:
            df[t] = df.get(t, 0) + 1
        index_docs.append({"id": d["id"], "text": d["text"]})
    
    idx = {"docs": index_docs, "df": df, "doc_len": doc_len, "n_docs": len(index_docs)}
    with open(KB_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    logger.info("KB index rebuilt: %d docs", len(index_docs))
    return idx

def load_kb_index():
    if os.path.exists(KB_INDEX_PATH):
        try:
            with open(KB_INDEX_PATH, "r", encoding="utf-8") as f:
                idx = json.load(f)
            if isinstance(idx, dict) and "docs" in idx:
                return idx
        except Exception:
            pass
    return rebuild_kb_index()

def bm25_score(query_tokens, doc_tokens, df, n_docs, k1=1.2, b=0.75, avgdl=200.0):
    score = 0.0
    freqs = {}
    for t in doc_tokens:
        freqs[t] = freqs.get(t, 0) + 1
    dl = float(len(doc_tokens)) or 1.0
    for t in query_tokens:
        if t not in freqs:
            continue
        n_qi = df.get(t, 0)
        idf = math.log(1.0 + (n_docs - n_qi + 0.5) / (n_qi + 0.5))
        tf = freqs[t]
        denom = tf + k1 * (1 - b + b * (dl / (avgdl or 1.0)))
        score += idf * ((tf * (k1 + 1)) / (denom or 1.0))
    return score

def retrieve_kb_snippets(query, idx, top_k=6, max_chars=1400):
    """RAG: получает 3-6 релевантных фрагментов."""
    docs = idx.get("docs") or []
    if not docs:
        return []
    
    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    
    df = idx.get("df") or {}
    n_docs = idx.get("n_docs") or max(1, len(docs))
    avgdl = 0.0
    if idx.get("doc_len"):
        avgdl = sum(idx["doc_len"].values()) / float(max(1, len(idx["doc_len"])))
    else:
        avgdl = 200.0
    
    scored = []
    for d in docs:
        dt = d.get("text", "")
        doc_tokens = tokenize(dt)
        s = bm25_score(q_tokens, doc_tokens, df, n_docs, avgdl=avgdl)
        if s > 0:
            scored.append((s, dt))
    scored.sort(key=lambda x: x[0], reverse=True)
    
    snippets = []
    for s, text in scored[:top_k]:
        t = clean_kb_markdown(text)
        t = t[:max_chars].strip()
        if t:
            snippets.append(t)
    return snippets

# -----------------------------
# GigaChat API
# -----------------------------
GIGACHAT_TOKEN_CACHE = {"token": None, "exp_ts": 0}

def gigachat_get_access_token(auth_key, scope, ca_bundle_path=None, timeout=30):
    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": "Basic " + auth_key,
    }
    verify = True
    if ca_bundle_path and os.path.exists(ca_bundle_path):
        verify = ca_bundle_path
    r = requests.post(url, headers=headers, data={"scope": scope}, timeout=timeout, verify=verify)
    r.raise_for_status()
    data = r.json()
    token = data.get("access_token") or ""
    exp = int(time.time()) + 25 * 60  # 25 минут для безопасности
    return token, exp

def gigachat_call(prompt, model=None, timeout=60):
    # Модель из .env или по умолчанию
    if model is None:
        model = os.getenv("GIGACHAT_MODEL", "GigaChat").strip()
    
    auth_key = os.getenv("GIGACHAT_AUTH_KEY", "").strip()
    scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
    ca_bundle = os.getenv("GIGACHAT_VERIFY_CA", "").strip() or os.getenv("GIGACHAT_CA_BUNDLE", "").strip()
    
    if not auth_key:
        return None, "GIGACHAT_AUTH_KEY not set"
    
    # refresh token if needed
    if (not GIGACHAT_TOKEN_CACHE["token"]) or (time.time() >= GIGACHAT_TOKEN_CACHE["exp_ts"]):
        try:
            token, exp = gigachat_get_access_token(auth_key, scope, ca_bundle_path=ca_bundle or None)
            GIGACHAT_TOKEN_CACHE["token"] = token
            GIGACHAT_TOKEN_CACHE["exp_ts"] = exp
        except Exception as e:
            return None, "GigaChat auth error: %s" % str(e)
    
    url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + GIGACHAT_TOKEN_CACHE["token"],
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Ты — AI-помощник по блокировкам счетов/карт, 115-ФЗ, ЗСК и комплаенсу. Отвечай кратко, структурировано, без markdown (#,*). Используй эмодзи ✅ 1️⃣ 2️⃣ 3️⃣ для читаемости. Не упоминай файлы/источники."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            return None, "No choices"
        msg = choices[0].get("message") or {}
        return msg.get("content") or "", None
    except Exception as e:
        return None, "GigaChat request error: %s" % str(e)

def build_llm_prompt(user_text, snippets, branch=None, case_context=None):
    """Строит промпт для GigaChat с контекстом кейса."""
    ctx = "\n\n".join(snippets) if snippets else ""
    branch_info = ""
    if branch:
        branch_info = "\nВетка кейса: %s" % branch
    case_info = ""
    if case_context:
        case_info = "\nКонтекст кейса: %s" % json.dumps(case_context, ensure_ascii=False)
    
    if ctx:
        return (
            "Вопрос пользователя: %s%s%s\n\n"
            "Фрагменты базы знаний (для опоры):\n%s\n\n"
            "Сформируй ответ. Если вопрос не по теме блокировок/115-ФЗ/ЗСК/комплаенса — мягко верни к теме и предложи 1 пример переформулировки. "
            "Если вопрос — термин/сокращение (например МФК/МВК/РКН/ФНС) и это связано с финансовой безопасностью/платежами/банками — дай определение. "
            "Ответ должен быть структурированным, с эмодзи, без markdown символов."
        ) % (user_text, branch_info, case_info, ctx)
    return (
        "Вопрос пользователя: %s%s%s\n\n"
        "Сформируй ответ. Если вопрос не по теме блокировок/115-ФЗ/ЗСК/комплаенса — мягко верни к теме и предложи 1 пример переформулировки. "
        "Ответ должен быть структурированным, с эмодзи, без markdown символов."
    ) % (user_text, branch_info, case_info)

# -----------------------------
# Branch detection (115-ФЗ, ЗСК, 161-ФЗ, налоги, приставы)
# -----------------------------
def detect_branch(text):
    """Определяет ветку кейса по тексту."""
    t = normalize_text(text)
    if "115" in t or "под" in t or "фт" in t or "подозрительн" in t:
        return "115fz"
    if "зск" in t or "зона" in t or "высокий риск" in t:
        return "zsk"
    if "161" in t or "согласие" in t or "перевод" in t:
        return "161fz"
    if "налог" in t or "фнс" in t or "таможн" in t or "тамож" in t:
        return "tax"
    if "пристав" in t or "исполнитель" in t or "фссп" in t:
        return "bailiffs"
    if "без объясн" in t or "не объясн" in t:
        return "no_reason"
    return None

# -----------------------------
# Anti-loop: отслеживание заданных вопросов
# -----------------------------
def was_question_asked(user_state, question_id):
    """Проверяет, был ли уже задан вопрос с таким ID."""
    asked = user_state.get("asked_questions", [])
    return question_id in asked

def mark_question_asked(user_id, question_id):
    """Помечает вопрос как заданный."""
    user_state, _ = get_user_state_persistent(user_id)
    asked = user_state.get("asked_questions", [])
    if question_id not in asked:
        asked.append(question_id)
        update_user_state_persistent(user_id, {"asked_questions": asked})

# -----------------------------
# Core handlers
# -----------------------------
def start(update: Update, context: CallbackContext):
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Обновляем last_chat_id и проверяем DM
    update_user_state_persistent(user.id, {
        "last_chat_id": chat_id,
        "dm_available": (chat_id == user.id),
    })
    
    update.message.reply_text(
        "Доброго времени суток! 👋\n\n"
        "Я помогу по блокировкам счетов/карт, 115‑ФЗ, ЗСК и комплаенсу.\n\n"
        "✅ Начните кейс: опишите ситуацию (что заблокировали и кто: банк/ФНС/приставы/ЦБ)\n"
        "📎 Или выберите меню: Раздатка/Шаблоны/Курсы",
        reply_markup=make_main_keyboard()
    )

def status(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Бот работает. Напишите вопрос или нажмите кнопку меню.", reply_markup=make_main_keyboard())

def handle_menu(update: Update, context: CallbackContext):
    """Обработка меню Раздатка/Шаблоны/Курсы."""
    text = (update.message.text or "").strip()
    content = context.bot_data.get("content") or load_content()
    context.bot_data["content"] = content
    
    if "раздат" in text.lower() or "раздач" in text.lower():
        items = content.get("handouts") or []
        msg = "📎 Раздатка:\n\n" + _format_items(items)
        update.message.reply_text(msg, reply_markup=make_main_keyboard())
        return True
    if "шаблон" in text.lower():
        items = content.get("templates") or []
        msg = "🧾 Шаблоны:\n\n" + _format_items(items)
        update.message.reply_text(msg, reply_markup=make_main_keyboard())
        return True
    if "курс" in text.lower():
        items = content.get("courses") or []
        msg = "📚 Курсы:\n\n" + _format_items(items)
        update.message.reply_text(msg, reply_markup=make_main_keyboard())
        return True
    return False

def handle_text(update: Update, context: CallbackContext):
    user = update.effective_user
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    
    logger.info("msg from %s(%s): %s", user.username or user.first_name, user.id, text)
    
    # Сохраняем сообщение пользователя в dialogs.jsonl
    safe_write_jsonl(DIALOGS_LOG, {
        "ts": now_ts(),
        "user_id": user.id,
        "user_name": user.username or user.first_name,
        "chat_id": chat_id,
        "role": "user",
        "text": text,
        "meta": {}
    })
    
    # Получаем состояние пользователя
    user_state, _ = get_user_state_persistent(user.id)
    
    # Обновляем last_chat_id и dm_available
    update_user_state_persistent(user.id, {
        "last_chat_id": chat_id,
        "dm_available": (chat_id == user.id),
        "last_user_message_ts": now_ts(),
    })
    
    # Ожидание комментария?
    if user_state.get("awaiting_comment_for"):
        ans_id = user_state["awaiting_comment_for"]
        update_user_state_persistent(user.id, {"awaiting_comment_for": None})
        
        # Получаем метаданные последнего ответа
        last_meta = user_state.get("last_answer_meta", {})
        
        # Сохраняем комментарий с метаданными
        safe_write_jsonl(FEEDBACK_LOG, {
            "ts": now_iso(),
            "user_id": user.id,
            "chat_id": chat_id,
            "message_id_bot": last_meta.get("message_id_bot"),
            "rating": None,
            "is_paid_star": False,
            "comment": text,
            "thread_id": user_state.get("thread_id"),
            "branch": user_state.get("branch"),
            "query_hash": last_meta.get("query_hash"),
            "rag_used": last_meta.get("rag_used", False),
            "gigachat_used": last_meta.get("gigachat_used", False),
            "answer_id": ans_id,
        })
        
        update.message.reply_text("Спасибо! Комментарий записан ✅", reply_markup=make_main_keyboard())
        return
    
    # Меню
    if handle_menu(update, context):
        return
    
    # Приветствие
    if is_greeting(text):
        update.message.reply_text(
            "Доброго времени суток! 👋\n\n"
            "✅ Начните кейс: опишите ситуацию (что заблокировали и кто: банк/ФНС/приставы/ЦБ)\n"
            "📎 Или выберите меню: Раздатка/Шаблоны/Курсы",
            reply_markup=make_main_keyboard()
        )
        return
    
    # Оффтоп
    if is_offtopic(text):
        update.message.reply_text(
            "Я консультирую по блокировкам счетов/карт, 115‑ФЗ, ЗСК и комплаенсу. "
            "Опишите ваш кейс: что заблокировали и что написал банк.",
            reply_markup=make_main_keyboard()
        )
        return
    
    # Определяем ветку
    branch = detect_branch(text) or user_state.get("branch")
    if branch and branch != user_state.get("branch"):
        update_user_state_persistent(user.id, {"branch": branch})
        user_state["branch"] = branch
    
    # RAG: получаем фрагменты из KB
    kb_idx = context.bot_data.get("kb_index")
    if not kb_idx:
        kb_idx = load_kb_index()
        context.bot_data["kb_index"] = kb_idx
    
    snippets = retrieve_kb_snippets(text, kb_idx, top_k=6)
    rag_used = len(snippets) > 0
    
    # Строим промпт для GigaChat
    case_context = user_state.get("case_data", {})
    prompt = build_llm_prompt(text, snippets, branch=branch, case_context=case_context)
    
    # Вызываем GigaChat
    answer, err = gigachat_call(prompt)
    gigachat_used = (answer is not None and not err)
    
    # Fallback без LLM
    if err or not answer:
        if snippets:
            answer = snippets[0][:800] + ("..." if len(snippets[0]) > 800 else "")
        else:
            answer = (
                "Я консультирую по блокировкам счетов/карт, 115‑ФЗ, ЗСК и комплаенсу.\n\n"
                "✅ Опишите кейс: что заблокировали, когда, и что написал банк."
            )
    
    answer = prettify_answer(answer)
    
    # Генерируем ID ответа и thread_id
    answer_id = str(uuid.uuid4())
    thread_id = user_state.get("thread_id") or str(uuid.uuid4())
    if not user_state.get("thread_id"):
        update_user_state_persistent(user.id, {"thread_id": thread_id})
    
    # Хеш запроса для отслеживания
    query_hash = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    
    # Отправляем ответ
    msg = update.message.reply_text(answer, reply_markup=make_main_keyboard())
    message_id_bot = msg.message_id
    
    # Сохраняем ответ бота в dialogs.jsonl
    safe_write_jsonl(DIALOGS_LOG, {
        "ts": now_ts(),
        "user_id": user.id,
        "user_name": user.username or user.first_name,
        "chat_id": chat_id,
        "role": "bot",
        "text": answer,
        "thread_id": thread_id,
        "meta": {
            "answer_id": answer_id,
            "question": text,
            "branch": branch,
            "rag_used": rag_used,
            "gigachat_used": gigachat_used,
        }
    })
    
    # Отправляем клавиатуру с оценкой
    try:
        feedback_msg = context.bot.send_message(
            chat_id=chat_id,
            text="Оцените ответ:",
            reply_markup=build_feedback_keyboard(answer_id)
        )
        feedback_message_id = feedback_msg.message_id
    except Exception as e:
        logger.error("Failed to send feedback keyboard: %s", e)
        feedback_message_id = None
    
    # Сохраняем метаданные последнего ответа в состоянии
    update_user_state_persistent(user.id, {
        "last_answer_id": answer_id,
        "last_answer_meta": {
            "message_id_bot": message_id_bot,
            "query_hash": query_hash,
            "rag_used": rag_used,
            "gigachat_used": gigachat_used,
            "question": text,
        }
    })

def on_callback(update: Update, context: CallbackContext):
    """Обработка callback от inline-кнопок."""
    q = update.callback_query
    if not q:
        return
    q.answer()
    data = q.data or ""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    if data.startswith("FB:STAR:"):
        # FB:STAR:5:<answer_id>
        parts = data.split(":")
        try:
            stars = int(parts[2])
            ans_id = parts[3] if len(parts) >= 4 else None
        except Exception:
            return
        
        user_state, _ = get_user_state_persistent(user.id)
        
        # Получаем метаданные последнего ответа
        last_meta = user_state.get("last_answer_meta", {})
        
        # Сохраняем оценку с метаданными
        safe_write_jsonl(FEEDBACK_LOG, {
            "ts": now_iso(),
            "user_id": user.id,
            "chat_id": chat_id,
            "message_id_bot": last_meta.get("message_id_bot"),
            "rating": stars,
            "is_paid_star": (stars == 6),
            "comment": None,
            "thread_id": user_state.get("thread_id"),
            "branch": user_state.get("branch"),
            "query_hash": last_meta.get("query_hash"),
            "rag_used": last_meta.get("rag_used", False),
            "gigachat_used": last_meta.get("gigachat_used", False),
            "answer_id": ans_id,
        })
        
        if stars == 6:
            q.edit_message_text(
                "Спасибо за ⭐6 PRO! 🙌\n\n"
                "⭐6 — это платная оценка. Спасибо за поддержку!"
            )
        else:
            q.edit_message_text("Спасибо за оценку: %d⭐ ✅" % stars)
        return
    
    if data.startswith("FB:COMMENT:"):
        parts = data.split(":")
        ans_id = parts[2] if len(parts) >= 3 else None
        
        user_state, _ = get_user_state_persistent(user.id)
        update_user_state_persistent(user.id, {"awaiting_comment_for": ans_id})
        
        q.edit_message_text(
            "Напишите комментарий одним сообщением (что улучшить/что было непонятно).\n"
            "Или нажмите 'Пропустить' если не хотите оставлять комментарий."
        )
        return

# -----------------------------
# Admin commands
# -----------------------------
def is_admin(user_id):
    """Проверяет, является ли пользователь админом."""
    return user_id in ADMIN_IDS

def cmd_inbox(update: Update, context: CallbackContext):
    """Команда /inbox — список новых комментариев."""
    user = update.effective_user
    if not is_admin(user.id):
        update.message.reply_text("❌ Доступ запрещён.")
        return
    
    # Читаем feedback.jsonl
    comments = []
    if os.path.exists(FEEDBACK_LOG):
        with open(FEEDBACK_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("type") == "comment" or (event.get("comment") and event.get("comment").strip()):
                        comments.append(event)
                except Exception:
                    continue
    
    # Сортируем по времени (новые первыми)
    comments.sort(key=lambda x: x.get("ts", ""), reverse=True)
    
    # Показываем последние 10
    if not comments:
        update.message.reply_text("📭 Нет новых комментариев.")
        return
    
    msg_parts = ["📬 Последние комментарии:\n"]
    for i, c in enumerate(comments[:10], 1):
        thread_id = c.get("thread_id", "?")
        user_id = c.get("user_id", "?")
        comment = (c.get("comment") or c.get("text") or "")[:100]
        rating = c.get("rating")
        branch = c.get("branch", "?")
        
        msg_parts.append(
            "%d) Thread: %s | User: %s | Branch: %s | Rating: %s\n"
            "   Комментарий: %s\n" % (
                i, thread_id[:8], user_id, branch, rating or "—", comment
            )
        )
    
    update.message.reply_text("\n".join(msg_parts))

def cmd_reply(update: Update, context: CallbackContext):
    """Команда /reply <thread_id> <текст> — ответить пользователю."""
    user = update.effective_user
    if not is_admin(user.id):
        update.message.reply_text("❌ Доступ запрещён.")
        return
    
    args = context.args
    if len(args) < 2:
        update.message.reply_text("Использование: /reply <thread_id> <текст ответа>")
        return
    
    thread_id = args[0]
    reply_text = " ".join(args[1:])
    
    # Находим пользователя по thread_id
    target_user_id = None
    target_chat_id = None
    
    if os.path.exists(DIALOGS_LOG):
        with open(DIALOGS_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("thread_id") == thread_id:
                        target_user_id = event.get("user_id")
                        target_chat_id = event.get("chat_id")
                        break
                except Exception:
                    continue
    
    if not target_user_id:
        update.message.reply_text("❌ Пользователь с thread_id %s не найден." % thread_id)
        return
    
    # Пытаемся отправить в личку (приоритет)
    sent = False
    try:
        context.bot.send_message(chat_id=target_user_id, text=reply_text)
        sent = True
        update.message.reply_text("✅ Ответ отправлен в личку пользователю %s" % target_user_id)
    except Exception as e:
        logger.warning("Failed to send DM to %s: %s", target_user_id, e)
        # Fallback: отправляем в последний чат
        if target_chat_id:
            try:
                context.bot.send_message(chat_id=target_chat_id, text=reply_text)
                sent = True
                # Предлагаем кнопку для открытия лички
                update.message.reply_text(
                    "✅ Ответ отправлен в чат %s (личка недоступна).\n"
                    "Пользователь может открыть личку через /start" % target_chat_id
                )
            except Exception as e2:
                logger.error("Failed to send to chat %s: %s", target_chat_id, e2)
                update.message.reply_text("❌ Не удалось отправить ответ.")
    
    if sent:
        # Сохраняем ответ админа
        safe_write_jsonl(DIALOGS_LOG, {
            "ts": now_ts(),
            "user_id": target_user_id,
            "user_name": None,
            "chat_id": target_chat_id or target_user_id,
            "role": "admin",
            "text": reply_text,
            "thread_id": thread_id,
            "meta": {"admin_id": user.id}
        })
        
        # Обновляем состояние пользователя (dm_available)
        user_state, _ = get_user_state_persistent(target_user_id)
        update_user_state_persistent(target_user_id, {
            "dm_available": (target_chat_id == target_user_id) if target_chat_id else False
        })

# -----------------------------
# Error handler
# -----------------------------
def on_error(update: object, context: CallbackContext):
    logger.exception("Unhandled error: %s", context.error)

# -----------------------------
# Main
# -----------------------------
def main():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        logger.error("BOT_TOKEN is empty. Export BOT_TOKEN and restart.")
        return
    
    # Загружаем ID админов
    admin_str = os.getenv("ADMIN_IDS", "").strip()
    if admin_str:
        try:
            global ADMIN_IDS
            ADMIN_IDS = [int(x.strip()) for x in admin_str.split(",") if x.strip()]
            logger.info("Admin IDs: %s", ADMIN_IDS)
        except Exception as e:
            logger.warning("Failed to parse ADMIN_IDS: %s", e)
    
    # Предзагрузка content & kb
    try:
        content = load_content()
        logger.info("content.json loaded: handouts=%d templates=%d courses=%d",
                    len(content.get("handouts") or []),
                    len(content.get("templates") or []),
                    len(content.get("courses") or []))
    except Exception as e:
        logger.info("content.json load failed: %s", e)
    
    try:
        load_kb_index()
    except Exception as e:
        logger.info("KB index init failed: %s", e)
    
    updater = Updater(token=bot_token, use_context=True)
    dp = updater.dispatcher
    
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("status", status))
    dp.add_handler(CommandHandler("inbox", cmd_inbox))
    dp.add_handler(CommandHandler("reply", cmd_reply))
    dp.add_handler(CallbackQueryHandler(on_callback))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
    
    dp.add_error_handler(on_error)
    
    logger.info("Bot starting polling...")
    updater.start_polling(clean=True)
    updater.idle()

if __name__ == "__main__":
    main()
