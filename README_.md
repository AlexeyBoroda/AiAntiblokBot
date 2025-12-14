# AiAntiblokBot — Feedback + Dashboard (shared hosting, no ports)

Этот пакет добавляет:
- ⭐️ Оценку каждого ответа (1–5 звёзд + 6-я “платная”)
- 💬 Комментарий к ответу (тред по answer_id)
- 🧾 Логи событий в `data/feedback.jsonl` (JSONL: каждая строка — отдельный JSON)
- 💾 Лог переписки в `data/dialogs.jsonl` (JSONL)
- 📊 Статический дашборд в `dashboard/` (HTML + JSON)
- ✉️ Ответ админа пользователю из дашборда через `dashboard/reply.php` → `data/outbox.jsonl`
- ⏱️ Cron-скрипты:
  - `scripts/build_dashboard.py` → генерит `dashboard/stats.json` и `dashboard/comments.json`
  - `scripts/send_outbox.py` → отправляет накопленные ответы админа пользователям

## Переменные окружения (.env)
Обязательно:
- BOT_TOKEN=...

Опционально:
- ADMIN_IDS=243676537,11111111
- BOT_USERNAME=YourBotName   # без @, для deeplink в личку
- FEEDBACK_ENABLED=1
- FEEDBACK_MAX_STARS=5
- FEEDBACK_PAID_STAR=1
- OUTBOX_PATH=data/outbox.jsonl
- FEEDBACK_PATH=data/feedback.jsonl
- DIALOGS_PATH=data/dialogs.jsonl

## Установка
1) Распаковать архив в папку бота (где лежит ваш `data/`, `kb/`, `logs/`).
2) Заменить ваш `bot.py` на этот (или перенести блоки “FEEDBACK” в ваш код — но вы просили один файл, поэтому тут всё в одном).
3) Создать `dashboard/config.php` из `dashboard/config.php.example` и задать TOKEN.
4) Права на запись:
   - `data/` writable
   - `dashboard/` writable для cron (генерация json)

## Cron (пример)
* * * * * cd /home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot && ./venv/bin/python scripts/build_dashboard.py >/dev/null 2>&1
* * * * * cd /home/c/ck60067/borodulin.expert/public_html/my_script/AiAntiblokBot && ./venv/bin/python scripts/send_outbox.py >/dev/null 2>&1

## Как отвечать пользователям
1) Открываете `dashboard/index.html`
2) В треде пишете ответ и жмёте “Отправить”
3) Это пишет строку в `data/outbox.jsonl`
4) Cron `send_outbox.py` доставляет сообщение (DM → fallback)

