# -*- coding: utf-8 -*-
"""
AiAntiblokBot (python-telegram-bot==12.8, Python 3.6)

What this version fixes/improves:
- ✅ Menu restored: "📎 Раздатка / 🧾 Шаблон / 📚 Курс" loads from data/content.json (or kb/content.json) with safe fallbacks.
- ✅ RAG+GigaChat: retrieves relevant KB snippets (without showing filenames) and asks GigaChat to form a clean answer.
- ✅ Clean output: strips markdown symbols (#,*,`), makes readable short blocks + emojis.
- ✅ Anti-loop: prevents repeating the same question forever; advances the dialogue step or asks a different уточнение.
- ✅ Feedback: after every AI answer shows ⭐1..⭐5 + ⭐6 (платная) + 💬 Комментарий; logs to data/feedback.jsonl.
- ✅ Comments: user can leave a comment; saved to feedback log (threaded by answer_id).

Deploy: put this file as bot.py and restart watchdog.
"""

from __future__ import print_function

import os
import re
import json
import time
import uuid
import math
import logging
from datetime import datetime

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
FEEDBACK_LOG = os.path.join(DATA_DIR, "feedback.jsonl")

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

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

def safe_write_jsonl(path, event):
    line = json.dumps(event, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def normalize_text(s):
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def is_greeting(text):
    t = normalize_text(text)
    return t in ("привет", "здравствуй", "здравствуйте", "добрый день", "добрый вечер", "доброе утро", "хай", "hello")

def make_main_keyboard():
    kb = [["📎 Раздатка", "🧾 Шаблон", "📚 Курс"]]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def clean_kb_markdown(text):
    """Make KB snippets readable in Telegram (no raw markdown artifacts)."""
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
    """Format assistant output: short blocks + emojis, no markdown."""
    text = clean_kb_markdown(text)
    # keep it compact
    text = text.strip()

    # If long paragraph -> split a bit
    if len(text) > 900:
        # try split by sentences
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
    # ⭐6 as monetization option
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
# Content menu (content.json)
# -----------------------------
def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def load_content():
    """
    Tries multiple locations. Supports several structures:
    - {"handouts":[{"title":"..","url":".."}], "templates":[...], "courses":[...]}
    - {"Раздатка":[...], "Шаблоны":[...], "Курсы":[...]}
    """
    data = None
    for p in CONTENT_JSON_CANDIDATES:
        if os.path.exists(p):
            data = _load_json(p)
            if data:
                logger.info("Loaded content.json: %s", p)
                break
    if not isinstance(data, dict):
        data = {}

    # normalize keys
    out = {"handouts": [], "templates": [], "courses": []}
    # common variants
    for k in data.keys():
        lk = k.lower()
        if "раздат" in lk or "handout" in lk or "materials" in lk:
            out["handouts"] = data[k] or []
        elif "шаблон" in lk or "template" in lk:
            out["templates"] = data[k] or []
        elif "курс" in lk or "course" in lk:
            out["courses"] = data[k] or []

    # already normalized?
    if "handouts" in data and not out["handouts"]:
        out["handouts"] = data.get("handouts") or []
    if "templates" in data and not out["templates"]:
        out["templates"] = data.get("templates") or []
    if "courses" in data and not out["courses"]:
        out["courses"] = data.get("courses") or []

    # hard fallbacks (so no weird placeholders)
    if not out["courses"]:
        out["courses"] = [
            {"title": "Базовый курс (Stepik)", "url": "https://stepik.org/a/252040"},
            {"title": "Free (бесплатно)", "url": "https://stepik.org/a/252809"},
            {"title": "PRO", "url": "https://stepik.org/a/252823"},
        ]

    return out

def _format_items(items, max_n=10):
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
            if url:
                lines.append("%d) %s — %s" % (i, title, url))
            else:
                lines.append("%d) %s" % (i, title))
    return "\n".join(lines).strip()

# -----------------------------
# KB indexing (simple lexical search)
# -----------------------------
WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+")

def tokenize(text):
    text = (text or "").lower()
    return [w for w in WORD_RE.findall(text) if len(w) >= 2]

def load_kb_documents():
    docs = []
    # kb/text/*.md
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

    # kb/*.md (optional)
    if os.path.isdir(KB_DIR):
        for fn in os.listdir(KB_DIR):
            if fn.lower().endswith(".md"):
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
        logger.info("KB docs not found (kb/text). Index not rebuilt.")
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
    # simplified BM25
    score = 0.0
    freqs = {}
    for t in doc_tokens:
        freqs[t] = freqs.get(t, 0) + 1
    dl = float(len(doc_tokens)) or 1.0
    for t in query_tokens:
        if t not in freqs:
            continue
        n_qi = df.get(t, 0)
        # IDF with +1 smoothing
        idf = math.log(1.0 + (n_docs - n_qi + 0.5) / (n_qi + 0.5))
        tf = freqs[t]
        denom = tf + k1 * (1 - b + b * (dl / (avgdl or 1.0)))
        score += idf * ((tf * (k1 + 1)) / (denom or 1.0))
    return score

def retrieve_kb_snippets(query, idx, top_k=3, max_chars=1400):
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
        # take first informative chunk
        t = clean_kb_markdown(text)
        # cut to limit
        t = t[:max_chars].strip()
        if t:
            snippets.append(t)
    return snippets

# -----------------------------
# GigaChat API (access token refresh every 30 min)
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
    # token TTL ~30 min; keep 25 min to be safe
    exp = int(time.time()) + 25 * 60
    return token, exp

def gigachat_call(prompt, model="GigaChat", timeout=60):
    auth_key = os.getenv("GIGACHAT_AUTH_KEY", "").strip()
    scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
    ca_bundle = os.getenv("GIGACHAT_CA_BUNDLE", "").strip()  # e.g. data/ca/ca_bundle.pem

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
        # OpenAI-like
        choices = data.get("choices") or []
        if not choices:
            return None, "No choices"
        msg = choices[0].get("message") or {}
        return msg.get("content") or "", None
    except Exception as e:
        return None, "GigaChat request error: %s" % str(e)

def build_llm_prompt(user_text, snippets):
    ctx = "\n\n".join(snippets) if snippets else ""
    if ctx:
        return (
            "Вопрос пользователя: %s\n\n"
            "Фрагменты базы знаний (для опоры):\n%s\n\n"
            "Сформируй ответ. Если вопрос не по теме блокировок/115-ФЗ/ЗСК/комплаенса — мягко верни к теме и предложи 1 пример переформулировки. "
            "Если вопрос — термин/сокращение (например МФК/МВК/РКН/ФНС) и это связано с финансовой безопасностью/платежами/банками — дай определение."
        ) % (user_text, ctx)
    return (
        "Вопрос пользователя: %s\n\n"
        "Сформируй ответ. Если вопрос не по теме блокировок/115-ФЗ/ЗСК/комплаенса — мягко верни к теме и предложи 1 пример переформулировки."
    ) % user_text

# -----------------------------
# Dialogue state (anti-loop + case mode)
# -----------------------------
def get_user_state(context):
    return context.user_data.setdefault("state", {
        "case_step": 0,
        "last_bot_q": "",
        "repeat_count": 0,
        "awaiting_comment_for": None,
        "last_answer_id": None,
    })

def update_repeat_guard(state, bot_question):
    bot_question_n = normalize_text(bot_question)
    if bot_question_n and bot_question_n == normalize_text(state.get("last_bot_q", "")):
        state["repeat_count"] = int(state.get("repeat_count", 0)) + 1
    else:
        state["repeat_count"] = 0
        state["last_bot_q"] = bot_question
    return state["repeat_count"]

# -----------------------------
# Core handlers
# -----------------------------
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Доброго времени суток! 👋\n\n"
        "Я помогу по блокировкам счетов/карт, 115‑ФЗ, ЗСК и комплаенсу.\n"
        "Опишите ситуацию одной фразой (что именно заблокировали и кто: банк/ФНС/приставы/ЦБ).",
        reply_markup=make_main_keyboard()
    )

def status(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Бот работает. Напишите вопрос или нажмите кнопку меню.", reply_markup=make_main_keyboard())

def handle_menu(update: Update, context: CallbackContext):
    text = (update.message.text or "").strip()
    content = context.bot_data.get("content") or load_content()
    context.bot_data["content"] = content

    if "раздат" in text.lower() or "раздач" in text.lower():
        msg = "📎 Раздатка:\n" + _format_items(content.get("handouts") or [])
        update.message.reply_text(msg, reply_markup=make_main_keyboard())
        return True
    if "шаблон" in text.lower():
        msg = "🧾 Шаблоны:\n" + _format_items(content.get("templates") or [])
        update.message.reply_text(msg, reply_markup=make_main_keyboard())
        return True
    if "курс" in text.lower():
        msg = "📚 Курсы:\n" + _format_items(content.get("courses") or [])
        update.message.reply_text(msg, reply_markup=make_main_keyboard())
        return True
    return False

def handle_text(update: Update, context: CallbackContext):
    user = update.effective_user
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    logger.info("msg from %s(%s): %s", user.username or user.first_name, user.id, text)

    # awaiting comment?
    state = get_user_state(context)
    if state.get("awaiting_comment_for"):
        ans_id = state["awaiting_comment_for"]
        state["awaiting_comment_for"] = None
        event = {
            "ts": now_iso(),
            "type": "comment",
            "chat_id": chat_id,
            "user_id": user.id,
            "username": user.username,
            "answer_id": ans_id,
            "text": text,
        }
        safe_write_jsonl(FEEDBACK_LOG, event)
        update.message.reply_text("Спасибо! Комментарий записал ✅", reply_markup=make_main_keyboard())
        return

    # menu buttons
    if handle_menu(update, context):
        return

    # greeting
    if is_greeting(text):
        update.message.reply_text(
            "Доброго времени суток! 👋\n\n"
            "Скажите коротко: что заблокировали (счёт/карту/ДБО) и что написал банк/причина (если есть).",
            reply_markup=make_main_keyboard()
        )
        return

    # Build RAG context
    kb_idx = context.bot_data.get("kb_index")
    if not kb_idx:
        kb_idx = load_kb_index()
        context.bot_data["kb_index"] = kb_idx

    snippets = retrieve_kb_snippets(text, kb_idx, top_k=3)

    prompt = build_llm_prompt(text, snippets)
    answer, err = gigachat_call(prompt)

    # fallback without LLM if needed
    if err or not answer:
        # If KB has snippets, answer with first snippet compactly
        if snippets:
            answer = snippets[0]
        else:
            answer = (
                "Я консультирую по блокировкам счетов/карт, 115‑ФЗ, ЗСК и комплаенсу.\n"
                "Опишите кейс: что заблокировали, когда, и что написал банк."
            )

    answer = prettify_answer(answer)

    # anti-loop: if bot repeats itself too much, force a different clarifying question
    rep = update_repeat_guard(state, answer)
    if rep >= 2:
        answer = (
            "Похоже, я повторяюсь 🙃 Давайте иначе.\n\n"
            "✅ 1) Что именно ограничено: счёт/карта/ДБО?\n"
            "✅ 2) Формулировка банка (2–3 слова) или «без объяснений»?\n"
            "✅ 3) Вы — ИП/ООО или физлицо?"
        )
        state["repeat_count"] = 0
        state["last_bot_q"] = answer

    # send answer + feedback keyboard
    answer_id = str(uuid.uuid4())
    state["last_answer_id"] = answer_id

    update.message.reply_text(answer, reply_markup=make_main_keyboard())

    try:
        context.bot.send_message(
            chat_id=chat_id,
            text="Оцените ответ:",
            reply_markup=build_feedback_keyboard(answer_id)
        )
    except Exception:
        pass

def on_callback(update: Update, context: CallbackContext):
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
        stars = int(parts[2])
        ans_id = parts[3] if len(parts) >= 4 else None

        safe_write_jsonl(FEEDBACK_LOG, {
            "ts": now_iso(),
            "type": "rating",
            "chat_id": chat_id,
            "user_id": user.id,
            "username": user.username,
            "answer_id": ans_id,
            "stars": stars,
        })

        if stars >= 6:
            q.edit_message_text(
                "Спасибо за ⭐6! 🙌\n\n"
                "⭐6 — это «PRO‑оценка». Можно монетизировать это как донат/поддержку.\n"
                "Добавьте вашу ссылку на оплату/донат в тексте здесь (я оставил место)."
            )
        else:
            q.edit_message_text("Спасибо за оценку: %d⭐ ✅" % stars)
        return

    if data.startswith("FB:COMMENT:"):
        parts = data.split(":")
        ans_id = parts[2] if len(parts) >= 3 else None

        state = get_user_state(context)
        state["awaiting_comment_for"] = ans_id

        safe_write_jsonl(FEEDBACK_LOG, {
            "ts": now_iso(),
            "type": "comment_request",
            "chat_id": chat_id,
            "user_id": user.id,
            "username": user.username,
            "answer_id": ans_id,
        })

        q.edit_message_text("Напишите комментарий одним сообщением (что улучшить/что было непонятно).")
        return

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

    # preload content & kb
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
    dp.add_handler(CallbackQueryHandler(on_callback))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    dp.add_error_handler(on_error)

    logger.info("Bot starting polling...")
    updater.start_polling(clean=True)
    updater.idle()

if __name__ == "__main__":
    main()
