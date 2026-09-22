import os
import sys
import asyncio
import tempfile
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

test_db_path = os.path.join(tempfile.gettempdir(), f"test_planner_{uuid.uuid4().hex}.db")
os.environ["BOT_TOKEN"] = "123456789:TEST_BOT_TOKEN"
os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-testkey"
os.environ["TIMEZONE"] = "Asia/Yekaterinburg"
os.environ["DB_PATH"] = test_db_path

from app.database.session import (
    init_db,
    create_single_task,
    get_user_tasks,
    batch_update_task_timings
)
from app.utils.date_utils import get_now_yekt


async def test_planner_lifecycle():
    print("-> Тестирование Day Planner...")
    await init_db()

    user_id = 99999
    now = get_now_yekt()

    # 1. Создаем 3 тестовые задачи
    t1 = await create_single_task(user_id=user_id, title="Написать отчет по проекту", priority="high")
    t2 = await create_single_task(user_id=user_id, title="Созвон с клиентом", priority="medium")
    t3 = await create_single_task(user_id=user_id, title="Оплатить интернет", priority="low")

    tasks = await get_user_tasks(user_id=user_id, status="pending")
    assert len(tasks) >= 3

    # 2. Пакетное обновление расписания
    slot_1 = now + timedelta(hours=1)
    slot_2 = now + timedelta(hours=3)
    slot_3 = now + timedelta(hours=5)

    timings = [
        {"task_id": t1.id, "due_date": slot_1},
        {"task_id": t2.id, "due_date": slot_2},
        {"task_id": t3.id, "due_date": slot_3}
    ]

    updated_count = await batch_update_task_timings(user_id=user_id, task_timings=timings)
    assert updated_count == 3
    print(f"   [OK] Пакетное обновление {updated_count} задач выполнено успешно.")

    # 3. Проверка обновленных сроков
    updated_tasks = await get_user_tasks(user_id=user_id, status="pending")
    task_map = {t.id: t for t in updated_tasks}
    assert task_map[t1.id].due_date.strftime("%Y-%m-%d %H:%M") == slot_1.strftime("%Y-%m-%d %H:%M")
    assert task_map[t1.id].remind_at is not None
    print("   [OK] Сроки и напоминания корректно привязаны.")


if __name__ == "__main__":
    asyncio.run(test_planner_lifecycle())
    print("\n[SUCCESS] ALL PLANNER TESTS PASSED!")
