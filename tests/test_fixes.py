"""Регрессионные тесты для исправленных ошибок и системы администраторов."""
import os
import sys
import asyncio
import tempfile
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

test_db_path = os.path.join(tempfile.gettempdir(), f"test_fixes_{uuid.uuid4().hex}.db")
os.environ["BOT_TOKEN"] = "123456789:TEST_BOT_TOKEN_ABCDEFGHI"
os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-testkey"
os.environ["ALLOWED_TELEGRAM_IDS"] = "111111"
os.environ["ADMIN_TELEGRAM_IDS"] = ""
os.environ["TIMEZONE"] = "Asia/Yekaterinburg"
os.environ["DB_PATH"] = test_db_path

from aiogram import Bot, Dispatcher
from aiogram.types import Update, Message, Chat, User, Audio, Document

from app.config import settings
from app.utils.date_utils import get_now_yekt
from app.utils.text import split_message, MESSAGE_LIMIT
from app.database.session import (
    init_db,
    create_note_with_tasks,
    create_single_task,
    get_task_by_id,
    search_notes,
    delete_note_by_id,
    count_user_notes,
)
from app.services.access_service import access_service

USER_ID = 555


def test_admin_ids_fallback():
    # ADMIN_TELEGRAM_IDS пуст -> администраторы берутся из ALLOWED_TELEGRAM_IDS
    assert settings.admin_ids == {111111}
    assert access_service.is_admin(111111)
    assert not access_service.is_admin(222222)


def test_split_message():
    lines = [f"<b>Строка {i}</b> " + "x" * 90 for i in range(200)]
    text = "\n".join(lines)
    chunks = split_message(text)
    assert len(chunks) > 1
    assert all(len(c) <= MESSAGE_LIMIT for c in chunks)
    assert "\n".join(chunks) == text
    # Слишком длинная строка режется принудительно
    long_chunks = split_message("y" * (MESSAGE_LIMIT * 2 + 10))
    assert all(len(c) <= MESSAGE_LIMIT for c in long_chunks)


def _message(**kwargs) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=111111, type="private"),
        from_user=User(id=111111, is_bot=False, first_name="Admin"),
        **kwargs
    )


def test_extract_media_document_m4a():
    from app.handlers.voice import extract_media, is_meeting_caption

    msg = _message(
        document=Document(file_id="doc1", file_unique_id="u1", file_name="Созвон.m4a",
                          mime_type="audio/x-m4a", file_size=19 * 1024 * 1024),
        caption="/meeting"
    )
    media = extract_media(msg)
    assert media is not None and media.ext == "m4a" and media.file_id == "doc1"
    assert is_meeting_caption(msg)

    pdf = _message(document=Document(file_id="doc2", file_unique_id="u2", file_name="a.pdf", mime_type="application/pdf"))
    assert extract_media(pdf) is None


async def test_meeting_caption_routes_audio():
    """Раньше: аудио с подписью /meeting ловил Command-фильтр, а message.text был None -> падение и тишина."""
    import app.handlers.meeting as meeting
    from app.middlewares.auth import WhitelistMiddleware

    calls = []

    async def fake_process_audio_file(message, media, meeting_mode=False):
        calls.append((media.file_id, media.ext, meeting_mode))

    original = meeting.process_audio_file
    meeting.process_audio_file = fake_process_audio_file
    try:
        dp = Dispatcher()
        dp.message.outer_middleware(WhitelistMiddleware())
        dp.include_router(meeting.router)
        bot = Bot(token=settings.bot_token)

        for media_kwargs in (
            {"audio": Audio(file_id="aud1", file_unique_id="a1", duration=3600, file_name="call.m4a", file_size=19 * 1024 * 1024)},
            {"document": Document(file_id="doc1", file_unique_id="d1", file_name="call.m4a", mime_type="audio/mp4")},
        ):
            msg = _message(caption="/meeting", **media_kwargs)
            await dp.feed_update(bot, Update(update_id=1, message=msg))

        # Неавторизованный пользователь игнорируется
        stranger = Message(
            message_id=2, date=datetime.now(), chat=Chat(id=999, type="private"),
            from_user=User(id=999, is_bot=False, first_name="X"), caption="/meeting",
            audio=Audio(file_id="aud2", file_unique_id="a2", duration=10)
        )
        await dp.feed_update(bot, Update(update_id=2, message=stranger))
        await bot.session.close()
    finally:
        meeting.process_audio_file = original

    assert calls == [("aud1", "m4a", True), ("doc1", "m4a", True)]


async def test_db_fixes():
    await init_db()
    now = get_now_yekt()

    # 1. Даты из БД теперь aware и сравниваются с текущим временем (раньше TypeError)
    task = await create_single_task(USER_ID, "Тест", due_date=now + timedelta(hours=2))
    loaded = await get_task_by_id(task.id)
    assert loaded.due_date.tzinfo is not None
    assert loaded.due_date > now
    assert abs((loaded.due_date - (now + timedelta(hours=2))).total_seconds()) < 1

    # 2. Поиск по кириллице без учета регистра
    note, tasks = await create_note_with_tasks(
        USER_ID, "Встреча с Иваном", "Обсудили Договор поставки",
        tasks_data=[{"title": "Отправить договор", "due_date": now + timedelta(days=1)}]
    )
    assert len(await search_notes(USER_ID, "договор")) == 1
    assert len(await search_notes(USER_ID, "ИВАН")) == 1
    assert len(await search_notes(USER_ID, "100%")) == 0
    assert await count_user_notes(USER_ID) == 1

    # 3. Удаление заметки каскадно удаляет её задачи (раньше PRAGMA foreign_keys была выключена)
    assert await delete_note_by_id(note.id, USER_ID)
    assert await get_task_by_id(tasks[0].id) is None


async def test_access_service():
    await init_db()
    await access_service.load()
    assert not access_service.is_allowed(777)

    _, created = await access_service.add_user(777, added_by=111111, username="@ivan", full_name="Иван")
    assert created and access_service.is_allowed(777) and not access_service.is_admin(777)
    _, created_again = await access_service.add_user(777, added_by=111111)
    assert not created_again

    # Кэш восстанавливается из БД после перезапуска
    access_service._user_ids.clear()
    await access_service.load()
    assert access_service.is_allowed(777)
    assert 777 in access_service.recipient_ids()

    assert await access_service.remove_user(777)
    assert not access_service.is_allowed(777)
    assert access_service.is_allowed(111111)


async def main():
    test_admin_ids_fallback()
    test_split_message()
    test_extract_media_document_m4a()
    await test_meeting_caption_routes_audio()
    await test_db_fixes()
    await test_access_service()
    print("\n[SUCCESS] ALL FIX TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
