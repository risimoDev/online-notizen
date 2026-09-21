import os
import asyncio
import tempfile
from datetime import datetime, timedelta
import pytz

# Настраиваем фиктивные переменные окружения для тестов
os.environ["BOT_TOKEN"] = "123456789:TEST_BOT_TOKEN_ABCDEFGHI"
os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-testkey"
os.environ["ALLOWED_TELEGRAM_IDS"] = "111111,222222"
os.environ["TIMEZONE"] = "Asia/Yekaterinburg"
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_myzapis.db")

from app.config import settings
from app.utils.date_utils import (
    get_now_yekt,
    parse_datetime_yekt,
    calculate_remind_at,
    format_datetime_human
)
from app.database.session import (
    init_db,
    create_note_with_tasks,
    get_note_by_id,
    get_user_notes,
    search_notes,
    get_user_tasks,
    update_task_status,
    update_task_timing,
    delete_note_by_id,
    get_due_reminders
)


def test_config():
    print("-> Тестирование config.py...")
    assert settings.bot_token == "123456789:TEST_BOT_TOKEN_ABCDEFGHI"
    assert 111111 in settings.allowed_telegram_ids
    assert 222222 in settings.allowed_telegram_ids
    assert len(settings.fallback_models_list) > 0
    print("   [OK] Конфигурация успешно загружена.")


def test_date_utils():
    print("-> Тестирование date_utils.py (Asia/Yekaterinburg UTC+5)...")
    now = get_now_yekt()
    assert now.tzinfo is not None
    assert str(now.tzinfo) == "Asia/Yekaterinburg"

    # Тест парсинга строки даты
    parsed = parse_datetime_yekt("2026-09-25 15:30:00")
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.month == 9
    assert parsed.day == 25
    assert parsed.hour == 15
    assert parsed.minute == 30

    # Тест расчета времени напоминания за 15 минут
    due_date = now + timedelta(hours=2)
    remind_at = calculate_remind_at(due_date, lead_minutes=15)
    assert remind_at is not None
    diff_minutes = (due_date - remind_at).total_seconds() / 60
    assert diff_minutes == 15

    # Тест человекочитаемого форматирования
    formatted = format_datetime_human(now)
    assert "Сегодня в" in formatted
    print("   [OK] Утилиты времени работают корректно.")


async def test_database_flow():
    print("-> Тестирование базы данных (SQLite aiosqlite)...")
    await init_db()

    user_id = 111111
    now = get_now_yekt()
    task_due = now + timedelta(hours=3)

    # 1. Создание заметки с 2 задачами
    tasks_input = [
        {"title": "Позвонить поставщику", "due_date": task_due, "priority": "high"},
        {"title": "Купить канцелярию", "due_date": None, "priority": "low"}
    ]

    note, tasks = await create_note_with_tasks(
        user_id=user_id,
        title="Тестовая встреча с поставщиком",
        raw_content="Обсудили поставки на октябрь. Нужно позвонить поставщику и купить канцелярию.",
        summary="• Договорились о скидке 10%\n• Сроки поставок: до конца месяца",
        timeline="• 10:00 Начало встречи\n• 10:30 Согласование скидки",
        tags=["поставки", "работа"],
        tasks_data=tasks_input
    )

    assert note.id is not None
    assert len(tasks) == 2
    assert note.title == "Тестовая встреча с поставщиком"
    print(f"   [OK] Создана заметка #{note.id} с {len(tasks)} задачами.")

    # 2. Чтение заметки
    fetched_note = await get_note_by_id(note.id)
    assert fetched_note is not None
    assert len(fetched_note.tasks) == 2

    # 3. Поиск заметок
    search_res = await search_notes(user_id=user_id, query="поставки")
    assert len(search_res) >= 1
    assert search_res[0].id == note.id

    # 4. Проверка задач
    user_tasks = await get_user_tasks(user_id=user_id, status="pending")
    assert len(user_tasks) >= 2

    first_task = user_tasks[0]
    # Отмечаем как выполненную
    await update_task_status(first_task.id, status="completed")
    remaining_tasks = await get_user_tasks(user_id=user_id, status="pending")
    assert len(remaining_tasks) == len(user_tasks) - 1

    # 5. Удаление заметки (каскадно удаляет связанные задачи)
    await delete_note_by_id(note.id, user_id=user_id)
    assert await get_note_by_id(note.id) is None
    print("   [OK] Полный цикл CRUD в БД прошел успешно.")


async def main():
    test_config()
    test_date_utils()
    await test_database_flow()
    print("\n[SUCCESS] ALL TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    asyncio.run(main())
