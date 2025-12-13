#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb/rebuild_text_index.py
Сканирует kb/text/*.md и генерирует kb/text_index.json для быстрого поиска по базе знаний.

Идея MVP:
- режем на "чанки" по заголовкам и пустым строкам
- строим простой инвертированный индекс (term -> список (chunk_id, tf))
- считаем скоринг по TF-IDF (очень простой), достаточно для MVP
"""

import re
import json
import math
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
KB_DIR = BASE_DIR / "kb"
TEXT_DIR = KB_DIR / "text"
OUT_JSON = KB_DIR / "text_index.json"

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{2,}")

def tokenize(s: str):
    return [w.lower() for w in WORD_RE.findall(s or "")]

def chunk_markdown(md: str):
    lines = (md or "").splitlines()
    chunks = []
    title = None
    buf = []
    def flush():
        nonlocal buf, title
        txt = "\n".join(buf).strip()
        if txt:
            chunks.append({"title": title or "Без заголовка", "text": txt})
        buf = []
    for ln in lines:
        if ln.startswith("#"):
            flush()
            title = ln.lstrip("#").strip()
            continue
        if not ln.strip():
            # пустая строка — мягкий разделитель
            if len("\n".join(buf)) > 800:
                flush()
            else:
                buf.append("")
            continue
        buf.append(ln)
        if len("\n".join(buf)) > 1200:
            flush()
    flush()
    return chunks

def main():
    docs = []
    chunks = []
    if TEXT_DIR.exists():
        for p in sorted(TEXT_DIR.glob("*.md")):
            md = p.read_text(encoding="utf-8", errors="ignore")
            cks = chunk_markdown(md)
            docs.append({"doc_id": p.stem, "path": str(p.relative_to(BASE_DIR)).replace("\\", "/"), "title": cks[0]["title"] if cks else p.stem})
            for i, ck in enumerate(cks):
                chunks.append({
                    "chunk_id": f"{p.stem}::{i}",
                    "doc_id": p.stem,
                    "title": ck["title"],
                    "text": ck["text"],
                })

    # build df
    df = {}
    chunk_terms = {}
    for ck in chunks:
        terms = tokenize(ck["text"])
        chunk_terms[ck["chunk_id"]] = terms
        for t in set(terms):
            df[t] = df.get(t, 0) + 1

    N = max(len(chunks), 1)
    # postings: term -> list of (chunk_id, tf)
    postings = {}
    for ck in chunks:
        cid = ck["chunk_id"]
        terms = chunk_terms[cid]
        tf_map = {}
        for t in terms:
            tf_map[t] = tf_map.get(t, 0) + 1
        for t, tf in tf_map.items():
            postings.setdefault(t, []).append([cid, tf])

    obj = {
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "docs": docs,
        "chunks": chunks,
        "df": df,
        "postings": postings,
        "N": N,
    }
    OUT_JSON.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[OK] text_index.json generated:", OUT_JSON)
    print(" docs:", len(docs))
    print(" chunks:", len(chunks))
    print(" terms:", len(df))

if __name__ == "__main__":
    main()
