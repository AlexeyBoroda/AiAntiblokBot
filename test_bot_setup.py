#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки готовности бота к запуску.
Запускать в venv: venv/bin/python test_bot_setup.py
"""

import os
import sys
import json

def test_imports():
    """Проверка импортов."""
    print("1. Проверка импортов...")
    try:
        from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
        from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
        import requests
        from dotenv import load_dotenv
        print("   ✅ Все импорты успешны")
        return True
    except ImportError as e:
        print(f"   ❌ Ошибка импорта: {e}")
        return False

def test_env_vars():
    """Проверка переменных окружения."""
    print("\n2. Проверка переменных окружения...")
    from dotenv import load_dotenv
    load_dotenv()
    
    required = ["BOT_TOKEN", "GIGACHAT_AUTH_KEY"]
    optional = ["GIGACHAT_SCOPE", "GIGACHAT_CA_BUNDLE", "ADMIN_IDS"]
    
    all_ok = True
    for var in required:
        val = os.getenv(var)
        if val:
            print(f"   ✅ {var}: установлен")
        else:
            print(f"   ❌ {var}: НЕ установлен (обязательно!)")
            all_ok = False
    
    for var in optional:
        val = os.getenv(var)
        if val:
            print(f"   ✅ {var}: установлен")
        else:
            print(f"   ⚠️  {var}: не установлен (опционально)")
    
    return all_ok

def test_files():
    """Проверка наличия файлов."""
    print("\n3. Проверка файлов...")
    
    files_to_check = [
        ("bot.py", True),
        ("kb/content.json", True),
        ("kb/text", True, "dir"),
        ("data", True, "dir"),
        ("logs", True, "dir"),
        ("scripts/build_dashboard.py", True),
    ]
    
    all_ok = True
    for item in files_to_check:
        path = item[0]
        required = item[1]
        check_type = item[2] if len(item) > 2 else "file"
        
        exists = os.path.isdir(path) if check_type == "dir" else os.path.exists(path)
        if exists:
            if check_type == "dir":
                count = len([f for f in os.listdir(path) if f.endswith('.md')]) if path == "kb/text" else "OK"
                print(f"   ✅ {path}: существует" + (f" ({count} файлов)" if isinstance(count, int) else ""))
            else:
                print(f"   ✅ {path}: существует")
        else:
            if required:
                print(f"   ❌ {path}: НЕ найден (обязательно!)")
                all_ok = False
            else:
                print(f"   ⚠️  {path}: не найден (опционально)")
    
    return all_ok

def test_content_json():
    """Проверка content.json."""
    print("\n4. Проверка content.json...")
    try:
        with open("kb/content.json", "r", encoding="utf-8") as f:
            content = json.load(f)
        
        handouts = len(content.get("handouts", []))
        templates = len(content.get("templates", []))
        courses = len(content.get("courses", []))
        
        print(f"   ✅ Раздатка: {handouts} элементов")
        print(f"   ✅ Шаблоны: {templates} элементов")
        print(f"   ✅ Курсы: {courses} элементов")
        
        if handouts == 0 and templates == 0 and courses == 0:
            print("   ⚠️  Все списки пусты!")
            return False
        
        return True
    except Exception as e:
        print(f"   ❌ Ошибка чтения content.json: {e}")
        return False

def test_state_file():
    """Проверка создания state.json."""
    print("\n5. Проверка state.json...")
    try:
        # Импортируем функции из bot.py
        sys.path.insert(0, '.')
        from bot import get_user_state_persistent, STATE_FILE
        
        # Тестовый user_id
        test_user_id = 999999999
        state, _ = get_user_state_persistent(test_user_id)
        
        required_keys = ["branch", "case_data", "asked_questions", "thread_id"]
        all_keys = all(key in state for key in required_keys)
        
        if all_keys:
            print(f"   ✅ state.json создаётся правильно")
            print(f"   ✅ Все ключи присутствуют: {', '.join(required_keys)}")
            
            # Удаляем тестового пользователя
            if os.path.exists(STATE_FILE):
                state_dict = {}
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state_dict = json.load(f)
                if str(test_user_id) in state_dict:
                    del state_dict[str(test_user_id)]
                    with open(STATE_FILE, "w", encoding="utf-8") as f:
                        json.dump(state_dict, f, ensure_ascii=False, indent=2)
            
            return True
        else:
            print(f"   ❌ Отсутствуют ключи: {[k for k in required_keys if k not in state]}")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_dashboard():
    """Проверка генерации дашборда."""
    print("\n6. Проверка build_dashboard.py...")
    try:
        # Просто проверяем, что скрипт можно импортировать
        sys.path.insert(0, 'scripts')
        import build_dashboard
        print("   ✅ build_dashboard.py импортируется успешно")
        print("   ℹ️  Для генерации дашборда запустите: python scripts/build_dashboard.py")
        return True
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return False

def main():
    print("=" * 60)
    print("Тестирование готовности AiAntiblokBot к запуску")
    print("=" * 60)
    
    results = []
    results.append(("Импорты", test_imports()))
    results.append(("Переменные окружения", test_env_vars()))
    results.append(("Файлы", test_files()))
    results.append(("content.json", test_content_json()))
    results.append(("state.json", test_state_file()))
    results.append(("Дашборд", test_dashboard()))
    
    print("\n" + "=" * 60)
    print("Итоги:")
    print("=" * 60)
    
    all_ok = True
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
        if not result:
            all_ok = False
    
    print("=" * 60)
    if all_ok:
        print("✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ! Бот готов к запуску.")
        print("\nДля запуска:")
        print("  python bot.py")
        print("\nИли через watchdog:")
        print("  bash data/watchdog.sh")
    else:
        print("❌ ЕСТЬ ПРОБЛЕМЫ! Исправьте ошибки перед запуском.")
    
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())

