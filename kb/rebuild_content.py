#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
kb/rebuild_content.py
Сканирует:
- kb/handouts
- kb/templates
и генерирует kb/content.json для бота.

Правила:
- Берём только файлы (без папок)
- title = имя файла без расширения (можно переименовать файл как нужно для красивого пункта меню)
- id = стабильный (sha1 от relpath) чтобы не ломать callback_data
- relpath = "kb/handouts/..." или "kb/templates/..."

Запуск:
  python3 kb/rebuild_content.py
"""

import json
import hashlib
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
KB_DIR = BASE_DIR / "kb"
OUT = KB_DIR / "content.json"


def make_id(relpath):
    return hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:12]


def scan_dir(subdir):
    root = KB_DIR / subdir
    items = []
    if not root.exists():
        return items

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        # пропускаем скрытые/служебные
        if p.name.startswith(".") or p.name.endswith("~"):
            continue

        relpath = str(Path("kb") / subdir / p.relative_to(root)).replace("\\", "/")
        title = p.stem
        items.append({
            "id": make_id(relpath),
            "title": title,
            "filename": p.name,
            "relpath": relpath,
            "bytes": p.stat().st_size,
        })
    return items


def main():
    data = {
        "handouts": scan_dir("handouts"),
        "templates": scan_dir("templates"),
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("OK: wrote", OUT, "handouts=", len(data["handouts"]), "templates=", len(data["templates"]))


if __name__ == "__main__":
    main()
