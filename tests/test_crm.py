import os
import sys
import asyncio
import tempfile
import uuid
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

test_db_path = os.path.join(tempfile.gettempdir(), f"test_crm_{uuid.uuid4().hex}.db")
os.environ["BOT_TOKEN"] = "123456789:TEST_BOT_TOKEN"
os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-testkey"
os.environ["TIMEZONE"] = "Asia/Yekaterinburg"
os.environ["DB_PATH"] = test_db_path

from app.database.session import (
    init_db,
    upsert_contact_from_ai,
    get_user_contacts,
    get_contact_by_name,
    get_contact_by_id,
    delete_contact_by_id
)


async def test_crm_lifecycle():
    print("-> Тестирование персональной CRM...")
    await init_db()

    user_a = 10001
    user_b = 20002

    # 1. Добавляем контакт для user_a
    person_1 = {
        "name": "Андрей Смирнов",
        "role": "CTO в стартапе",
        "facts": "Перешел в финтех, дочка пошла в школу",
        "agreements": "Договорились списаться в ноябре насчет интеграции API",
        "birthday": "15 ноября",
        "contact_info": "@andrey_tech"
    }

    contact = await upsert_contact_from_ai(user_id=user_a, person_data=person_1)
    assert contact is not None
    assert contact.name == "Андрей Смирнов"
    assert contact.normalized_name == "андрей смирнов"
    assert contact.role_or_company == "CTO в стартапе"
    assert len(contact.interactions) == 1
    print("   [OK] Контакт успешно создан.")

    # 2. Обновляем контакт новыми фактами (дедупликация)
    person_1_update = {
        "name": "андрей смирнов",
        "facts": "Запустил бета-тестирование продукта",
        "agreements": "Обещал выслать демо-доступ"
    }
    updated = await upsert_contact_from_ai(user_id=user_a, person_data=person_1_update)
    assert updated.id == contact.id
    assert "бета-тестирование" in updated.notes_summary
    assert "демо-доступ" in updated.agreements
    print("   [OK] Факты и договоренности успешно объединены.")

    # 3. Поиск контакта по неполному имени
    found = await get_contact_by_name(user_id=user_a, query_name="андрей")
    assert found is not None
    assert found.id == contact.id
    print("   [OK] Нечеткий поиск контакта работает.")

    # 4. Проверка изоляции по user_id (user_b не должен видеть контакты user_a)
    contacts_b = await get_user_contacts(user_id=user_b)
    assert len(contacts_b) == 0

    found_by_b = await get_contact_by_name(user_id=user_b, query_name="Андрей")
    assert found_by_b is None
    print("   [OK] Изоляция между пользователями подтверждена.")

    # 5. Удаление контакта
    deleted = await delete_contact_by_id(contact_id=contact.id, user_id=user_a)
    assert deleted is True
    assert await get_contact_by_id(contact.id, user_id=user_a) is None
    print("   [OK] Удаление контакта работает корректно.")


if __name__ == "__main__":
    asyncio.run(test_crm_lifecycle())
    print("\n[SUCCESS] ALL CRM TESTS PASSED!")
