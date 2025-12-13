# AiAntiblokBot — MVP (Python 3.6) + GigaChat + KB

## Быстрый старт

1) Заполните `.env` рядом с `bot.py`:
```env
BOT_TOKEN=...
REQUIRED_CHANNEL=@Borodulin_expert

GIGACHAT_AUTH_KEY=Basic <Authorization key>
GIGACHAT_SCOPE=GIGACHAT_API_PERS
```

2) Сборка материалов:
```bash
./venv/bin/python kb/rebuild_content.py
./venv/bin/python kb/rebuild_text_index.py
```

3) Запуск:
```bash
./venv/bin/python bot.py
```

## Папки

- `kb/handouts/` — раздатка (файлы)
- `kb/templates/` — шаблоны (файлы)
- `kb/text/` — база знаний (md)
- `kb/content.json` — генерится скриптом
- `kb/text_index.json` — индекс для поиска по базе знаний
