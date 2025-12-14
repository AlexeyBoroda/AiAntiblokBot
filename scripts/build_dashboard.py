#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор статического HTML дашборда для AiAntiblokBot.

Читает feedback.jsonl и dialogs.jsonl, генерирует:
- dashboard/index.html (статический HTML с фильтрами)
- dashboard/stats.json
- dashboard/comments.json
"""

import json
import os
import time
from collections import defaultdict
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DASH_DIR = os.path.join(BASE_DIR, "dashboard")

FEEDBACK_PATH = os.getenv("FEEDBACK_PATH", os.path.join(DATA_DIR, "feedback.jsonl"))
DIALOGS_PATH = os.getenv("DIALOGS_PATH", os.path.join(DATA_DIR, "dialogs.jsonl"))

def read_jsonl(path):
    """Читает JSONL файл."""
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

def parse_ts(ts_str):
    """Парсит timestamp (ISO или int)."""
    if isinstance(ts_str, int):
        return ts_str
    if isinstance(ts_str, str):
        try:
            # ISO format: 2024-01-01T12:00:00Z
            if "T" in ts_str:
                dt = datetime.strptime(ts_str.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
                return int(dt.timestamp())
            return int(ts_str)
        except Exception:
            pass
    return 0

def ts_str(ts):
    """Форматирует timestamp в строку."""
    try:
        ts_val = parse_ts(ts)
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_val))
    except Exception:
        return str(ts)

def escape_html(text):
    """Экранирует HTML."""
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;"))

def main():
    fb = read_jsonl(FEEDBACK_PATH)
    dialogs = read_jsonl(DIALOGS_PATH)
    
    # Собираем Q&A из dialogs
    qa = {}
    user_name = {}
    thread_to_user = {}
    
    for e in dialogs:
        uid = e.get("user_id")
        if uid:
            if e.get("user_name"):
                user_name[int(uid)] = e.get("user_name")
            thread_id = e.get("thread_id")
            if thread_id:
                thread_to_user[thread_id] = int(uid)
        
        # Ответы бота
        if e.get("role") == "bot":
            meta = e.get("meta", {})
            answer_id = meta.get("answer_id")
            if answer_id:
                qa[answer_id] = {
                    "question": meta.get("question", ""),
                    "answer": e.get("text", ""),
                    "user_id": int(uid or 0),
                    "user_name": e.get("user_name", ""),
                    "thread_id": thread_id,
                    "branch": meta.get("branch"),
                    "ts": e.get("ts", 0),
                }
    
    # Собираем оценки и комментарии
    ratings = defaultdict(list)
    comments = defaultdict(list)
    admin_replies = defaultdict(list)
    
    for e in fb:
        aid = e.get("answer_id")
        if not aid:
            # Может быть привязан к thread_id
            thread_id = e.get("thread_id")
            if thread_id:
                # Ищем answer_id по thread_id
                for a_id, qa_data in qa.items():
                    if qa_data.get("thread_id") == thread_id:
                        aid = a_id
                        break
        
        if not aid:
            continue
        
        rating = e.get("rating")
        if rating is not None:
            ratings[aid].append(int(rating))
        
        comment = e.get("comment") or e.get("text")
        if comment and comment.strip():
            comments[aid].append({
                "text": comment,
                "ts": parse_ts(e.get("ts", 0)),
                "ts_str": ts_str(e.get("ts", 0)),
                "user_id": e.get("user_id"),
            })
    
    # Собираем ответы админов из dialogs
    for e in dialogs:
        if e.get("role") == "admin":
            thread_id = e.get("thread_id")
            if thread_id:
                # Находим answer_id по thread_id
                for a_id, qa_data in qa.items():
                    if qa_data.get("thread_id") == thread_id:
                        admin_replies[a_id].append({
                            "text": e.get("text", ""),
                            "ts": e.get("ts", 0),
                            "ts_str": ts_str(e.get("ts", 0)),
                        })
                        break
    
    # Формируем треды
    threads = []
    total_ratings = 0
    paid_6 = 0
    sum_1_5 = 0
    cnt_1_5 = 0
    answers_with_rating = 0
    branch_stats = defaultdict(int)
    
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
        avg_1_5 = (sum(r15) / len(r15)) if r15 else 0.0
        
        branch = base.get("branch") or "unknown"
        branch_stats[branch] += 1
        
        uid = int(base.get("user_id") or 0)
        uname = base.get("user_name") or user_name.get(uid, "")
        
        # Статус: новый/в работе/закрыт
        status = "новый"
        if a:
            status = "закрыт"
        elif c:
            status = "в работе"
        
        threads.append({
            "answer_id": aid,
            "thread_id": base.get("thread_id", ""),
            "user_id": uid,
            "user_name": uname,
            "question": (base.get("question") or "")[:2000],
            "answer": (base.get("answer") or "")[:4000],
            "ratings": {
                "count": len(r),
                "avg_1_5": round(avg_1_5, 2),
                "paid_6": sum(1 for x in r if x == 6),
                "all": r,
            },
            "comments": c[-50:],
            "admin_replies": a[-50:],
            "branch": branch,
            "ts": base.get("ts", 0),
            "ts_str": ts_str(base.get("ts", 0)),
            "status": status,
        })
    
    # Сортируем треды (новые комментарии первыми)
    threads.sort(key=lambda x: (
        x["comments"][-1]["ts"] if x["comments"] else 0,
        x["ratings"]["count"]
    ), reverse=True)
    
    # Статистика
    stats = {
        "generated_ts": int(time.time()),
        "generated_ts_str": ts_str(int(time.time())),
        "answers_with_rating": answers_with_rating,
        "total_ratings": total_ratings,
        "avg_rating_1_5": round((sum_1_5 / cnt_1_5) if cnt_1_5 else 0.0, 2),
        "paid_star_6": paid_6,
        "threads": len(threads),
        "branch_stats": dict(branch_stats),
    }
    
    # Сохраняем JSON
    os.makedirs(DASH_DIR, exist_ok=True)
    with open(os.path.join(DASH_DIR, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DASH_DIR, "comments.json"), "w", encoding="utf-8") as f:
        json.dump({
            "generated_ts": int(time.time()),
            "threads": threads
        }, f, ensure_ascii=False, indent=2)
    
    # Генерируем HTML
    html_content = generate_html(stats, threads)
    with open(os.path.join(DASH_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print("Dashboard generated: %s/index.html" % DASH_DIR)
    print("Stats: %d threads, %d ratings, avg %.2f" % (
        len(threads), total_ratings, stats["avg_rating_1_5"]
    ))

def generate_html(stats, threads):
    """Генерирует статический HTML дашборд."""
    
    # Подготавливаем данные для JS
    threads_json = json.dumps(threads, ensure_ascii=False)
    stats_json = json.dumps(stats, ensure_ascii=False)
    
    html = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AiAntiblokBot Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f5;
            padding: 20px;
            color: #333;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { margin-bottom: 20px; color: #2c3e50; }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .stat-card h3 { font-size: 14px; color: #666; margin-bottom: 10px; }
        .stat-card .value { font-size: 32px; font-weight: bold; color: #2c3e50; }
        .filters {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .filters label { display: inline-block; margin-right: 15px; margin-bottom: 10px; }
        .filters select, .filters input {
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
            margin-left: 5px;
        }
        .filters input[type="checkbox"] { margin-left: 5px; }
        table {
            width: 100%;
            background: white;
            border-collapse: collapse;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            border-radius: 8px;
            overflow: hidden;
        }
        thead { background: #2c3e50; color: white; }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }
        tbody tr:hover { background: #f9f9f9; }
        .status-new { color: #e74c3c; font-weight: bold; }
        .status-work { color: #f39c12; font-weight: bold; }
        .status-closed { color: #27ae60; font-weight: bold; }
        .rating { display: inline-block; margin-right: 5px; }
        .comment-preview { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .thread-details { display: none; margin-top: 10px; padding: 10px; background: #f9f9f9; border-radius: 4px; }
        .thread-details.show { display: block; }
        .thread-details h4 { margin-bottom: 10px; }
        .thread-details p { margin: 5px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 AiAntiblokBot Dashboard</h1>
        
        <div class="stats">
            <div class="stat-card">
                <h3>Всего ответов</h3>
                <div class="value" id="stat-total">-</div>
            </div>
            <div class="stat-card">
                <h3>Средняя оценка (1-5)</h3>
                <div class="value" id="stat-avg">-</div>
            </div>
            <div class="stat-card">
                <h3>⭐6 (платная)</h3>
                <div class="value" id="stat-paid">-</div>
            </div>
            <div class="stat-card">
                <h3>Тредов</h3>
                <div class="value" id="stat-threads">-</div>
            </div>
        </div>
        
        <div class="filters">
            <label>
                Период:
                <input type="date" id="filter-date-from">
                -
                <input type="date" id="filter-date-to">
            </label>
            <label>
                Ветка:
                <select id="filter-branch">
                    <option value="">Все</option>
                </select>
            </label>
            <label>
                Рейтинг:
                <select id="filter-rating">
                    <option value="">Все</option>
                    <option value="1">⭐1</option>
                    <option value="2">⭐2</option>
                    <option value="3">⭐3</option>
                    <option value="4">⭐4</option>
                    <option value="5">⭐5</option>
                    <option value="6">⭐6 PRO</option>
                </select>
            </label>
            <label>
                <input type="checkbox" id="filter-comments-only"> Только с комментариями
            </label>
            <label>
                <input type="checkbox" id="filter-paid-only"> Только ⭐6
            </label>
            <label>
                Статус:
                <select id="filter-status">
                    <option value="">Все</option>
                    <option value="новый">Новый</option>
                    <option value="в работе">В работе</option>
                    <option value="закрыт">Закрыт</option>
                </select>
            </label>
        </div>
        
        <table>
            <thead>
                <tr>
                    <th>Дата</th>
                    <th>User ID</th>
                    <th>Ветка</th>
                    <th>Рейтинг</th>
                    <th>Комментарий</th>
                    <th>Статус</th>
                    <th>Действия</th>
                </tr>
            </thead>
            <tbody id="threads-table">
            </tbody>
        </table>
    </div>
    
    <script>
        const stats = """ + stats_json + """;
        const threads = """ + threads_json + """;
        
        // Обновляем статистику
        document.getElementById('stat-total').textContent = stats.threads;
        document.getElementById('stat-avg').textContent = stats.avg_rating_1_5.toFixed(2);
        document.getElementById('stat-paid').textContent = stats.paid_star_6;
        document.getElementById('stat-threads').textContent = stats.threads;
        
        // Заполняем фильтр веток
        const branches = [...new Set(threads.map(t => t.branch).filter(Boolean))];
        const branchSelect = document.getElementById('filter-branch');
        branches.forEach(b => {
            const opt = document.createElement('option');
            opt.value = b;
            opt.textContent = b;
            branchSelect.appendChild(opt);
        });
        
        // Функция фильтрации
        function filterThreads() {
            const dateFrom = document.getElementById('filter-date-from').value;
            const dateTo = document.getElementById('filter-date-to').value;
            const branch = document.getElementById('filter-branch').value;
            const rating = document.getElementById('filter-rating').value;
            const commentsOnly = document.getElementById('filter-comments-only').checked;
            const paidOnly = document.getElementById('filter-paid-only').checked;
            const status = document.getElementById('filter-status').value;
            
            let filtered = threads.filter(t => {
                if (branch && t.branch !== branch) return false;
                if (status && t.status !== status) return false;
                if (commentsOnly && (!t.comments || t.comments.length === 0)) return false;
                if (paidOnly && t.ratings.paid_6 === 0) return false;
                if (rating) {
                    const hasRating = t.ratings.all && t.ratings.all.includes(parseInt(rating));
                    if (!hasRating) return false;
                }
                if (dateFrom || dateTo) {
                    const ts = t.ts || 0;
                    const date = new Date(ts * 1000);
                    if (dateFrom) {
                        const from = new Date(dateFrom);
                        if (date < from) return false;
                    }
                    if (dateTo) {
                        const to = new Date(dateTo);
                        to.setHours(23, 59, 59);
                        if (date > to) return false;
                    }
                }
                return true;
            });
            
            renderTable(filtered);
        }
        
        // Рендеринг таблицы
        function renderTable(threadsToShow) {
            const tbody = document.getElementById('threads-table');
            tbody.innerHTML = '';
            
            threadsToShow.forEach(t => {
                const tr = document.createElement('tr');
                const ratingText = t.ratings.all && t.ratings.all.length > 0
                    ? t.ratings.all.map(r => '⭐' + r).join(' ')
                    : '—';
                const commentPreview = t.comments && t.comments.length > 0
                    ? t.comments[t.comments.length - 1].text.substring(0, 50) + '...'
                    : '—';
                const statusClass = 'status-' + (t.status === 'новый' ? 'new' : t.status === 'в работе' ? 'work' : 'closed');
                
                tr.innerHTML = `
                    <td>${escapeHtml(t.ts_str || '')}</td>
                    <td>${t.user_id}</td>
                    <td>${escapeHtml(t.branch || '—')}</td>
                    <td>${ratingText}</td>
                    <td class="comment-preview" title="${escapeHtml(commentPreview)}">${escapeHtml(commentPreview)}</td>
                    <td><span class="${statusClass}">${escapeHtml(t.status)}</span></td>
                    <td><button onclick="toggleDetails('${t.answer_id}')">Подробнее</button></td>
                `;
                tbody.appendChild(tr);
                
                // Детали треда
                const detailsTr = document.createElement('tr');
                detailsTr.id = 'details-' + t.answer_id;
                detailsTr.className = 'thread-details';
                detailsTr.innerHTML = `
                    <td colspan="7">
                        <div>
                            <h4>Вопрос:</h4>
                            <p>${escapeHtml(t.question || '—')}</p>
                            <h4>Ответ:</h4>
                            <p>${escapeHtml(t.answer || '—')}</p>
                            <h4>Комментарии:</h4>
                            ${t.comments && t.comments.length > 0
                                ? t.comments.map(c => `<p><strong>${escapeHtml(c.ts_str)}:</strong> ${escapeHtml(c.text)}</p>`).join('')
                                : '<p>Нет комментариев</p>'}
                            <h4>Ответы админа:</h4>
                            ${t.admin_replies && t.admin_replies.length > 0
                                ? t.admin_replies.map(a => `<p><strong>${escapeHtml(a.ts_str)}:</strong> ${escapeHtml(a.text)}</p>`).join('')
                                : '<p>Нет ответов</p>'}
                        </div>
                    </td>
                `;
                tbody.appendChild(detailsTr);
            });
        }
        
        function toggleDetails(answerId) {
            const details = document.getElementById('details-' + answerId);
            details.classList.toggle('show');
        }
        
        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        // Привязываем фильтры
        document.getElementById('filter-date-from').addEventListener('change', filterThreads);
        document.getElementById('filter-date-to').addEventListener('change', filterThreads);
        document.getElementById('filter-branch').addEventListener('change', filterThreads);
        document.getElementById('filter-rating').addEventListener('change', filterThreads);
        document.getElementById('filter-comments-only').addEventListener('change', filterThreads);
        document.getElementById('filter-paid-only').addEventListener('change', filterThreads);
        document.getElementById('filter-status').addEventListener('change', filterThreads);
        
        // Первоначальная загрузка
        filterThreads();
    </script>
</body>
</html>"""
    
    return html

if __name__ == "__main__":
    main()
