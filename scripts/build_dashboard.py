#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, time
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DASH_DIR = os.path.join(BASE_DIR, "dashboard")

FEEDBACK_PATH = os.getenv("FEEDBACK_PATH", os.path.join(DATA_DIR, "feedback.jsonl"))
DIALOGS_PATH = os.getenv("DIALOGS_PATH", os.path.join(DATA_DIR, "dialogs.jsonl"))

def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out

def ts_str(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return ""

def main():
    fb = read_jsonl(FEEDBACK_PATH)
    dialogs = read_jsonl(DIALOGS_PATH)

    qa = {}
    user_name = {}
    for e in dialogs:
        uid = e.get("user_id")
        if uid and e.get("user_name"):
            user_name[int(uid)] = e.get("user_name")
        if e.get("type") == "bot_answer" and e.get("answer_id"):
            qa[e["answer_id"]] = {
                "question": e.get("question",""),
                "answer": e.get("text",""),
                "user_id": int(e.get("user_id") or 0),
                "user_name": e.get("user_name") or "",
            }

    ratings = defaultdict(list)
    comments = defaultdict(list)
    admin_replies = defaultdict(list)

    for e in fb:
        aid = e.get("answer_id")
        if not aid:
            continue
        if e.get("type") == "rating":
            try: ratings[aid].append(int(e.get("rating") or 0))
            except Exception: pass
        elif e.get("type") == "comment":
            comments[aid].append({"text": e.get("text",""), "ts": e.get("ts",0), "ts_str": ts_str(e.get("ts",0))})
        elif e.get("type") == "admin_reply":
            admin_replies[aid].append({"text": e.get("text",""), "ts": e.get("ts",0), "ts_str": ts_str(e.get("ts",0))})

    threads = []
    total_ratings = 0
    paid_6 = 0
    sum_1_5 = 0
    cnt_1_5 = 0
    answers_with_rating = 0

    for aid, base in qa.items():
        r = ratings.get(aid, [])
        c = comments.get(aid, [])
        a = admin_replies.get(aid, [])

        if r:
            answers_with_rating += 1

        total_ratings += len(r)
        paid_6 += sum(1 for x in r if x == 6)
        r15 = [x for x in r if 1 <= x <= 5]
        if r15:
            sum_1_5 += sum(r15)
            cnt_1_5 += len(r15)
        avg_1_5 = (sum(r15)/len(r15)) if r15 else 0.0

        uid = int(base.get("user_id") or 0)
        uname = base.get("user_name") or user_name.get(uid,"")

        threads.append({
            "answer_id": aid,
            "user_id": uid,
            "user_name": uname,
            "question": (base.get("question") or "")[:2000],
            "answer": (base.get("answer") or "")[:4000],
            "ratings": {"count": len(r), "avg_1_5": avg_1_5, "paid_6": sum(1 for x in r if x==6)},
            "comments": c[-50:],
            "admin_replies": a[-50:],
        })

    threads.sort(key=lambda x: (x["comments"][-1]["ts"] if x["comments"] else 0, x["ratings"]["count"]), reverse=True)

    stats = {
        "generated_ts": int(time.time()),
        "answers_with_rating": answers_with_rating,
        "total_ratings": total_ratings,
        "avg_rating_1_5": (sum_1_5/cnt_1_5) if cnt_1_5 else 0.0,
        "paid_star_6": paid_6,
        "threads": len(threads),
    }

    os.makedirs(DASH_DIR, exist_ok=True)
    with open(os.path.join(DASH_DIR, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DASH_DIR, "comments.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_ts": int(time.time()), "threads": threads}, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
