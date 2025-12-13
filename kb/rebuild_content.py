#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
rebuild_content.py
Сканирует:
  kb/handouts
  kb/templates
и генерирует kb/content.json

Python 3.6+
"""

import os
import re
import json
import hashlib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]   # .../AiAntiblokBot
KB_DIR = BASE_DIR / "kb"
HANDOUTS_DIR = KB_DIR / "handouts"
TEMPLATES_DIR = KB_DIR / "templates"
CONTENT_JSON = KB_DIR / "content.json"

ALLOW_EXT = {".pdf", ".doc", ".docx", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".zip", ".rar"}

def _clean_title(name):
    # "Шаблон_№_1_Причины_ограничения_ДБО.DOCX" -> "Шаблон № 1 Причины ограничения ДБО"
    base = name
    base = re.sub(r"\.[A-Za-z0-9]{1,6}$", "", base)
    base = base.replace("_", " ")
    base = re.sub(r"\s+", " ", base).strip()
    return base

def _make_id(relpath):
    # стабильный id от относительного пути (чтобы не прыгал)
    h = hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:12]
    return h

def _scan_dir(root_dir, rel_prefix):
    items = []
    if not root_dir.exists():
        return items

    for p in sorted(root_dir.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in ALLOW_EXT:
            continue

        relpath = str(Path(rel_prefix) / p.relative_to(root_dir)).replace("\\", "/")
        item_id = _make_id(relpath)
        items.append({
            "id": item_id,
            "title": _clean_title(p.name),
            "filename": p.name,
            "ext": ext,
            "bytes": int(p.stat().st_size),
            "relpath": relpath,  # относительно BASE_DIR (например "kb/handouts/file.pdf")
        })
    return items

def main():
    KB_DIR.mkdir(parents=True, exist_ok=True)
    HANDOUTS_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    handouts = _scan_dir(HANDOUTS_DIR, "kb/handouts")
    templates = _scan_dir(TEMPLATES_DIR, "kb/templates")

    content = {
        "handouts": handouts,
        "templates": templates,
    }

    CONTENT_JSON.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[OK] written:", str(CONTENT_JSON))
    print("[OK] handouts:", len(handouts))
    print("[OK] templates:", len(templates))

if __name__ == "__main__":
    main()

