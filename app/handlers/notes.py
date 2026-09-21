import logging
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.enums import ChatAction
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.openrouter_service import ai_service
from app.database.session import (
    create_note_with_tasks,
    get_user_notes,
    get_note_by_id,
    delete_note_by_id,
    search_notes,
    get_user_tasks
)
from app.utils.date_utils import format_datetime_human
from app.utils.keyboards import get_note_created_keyboard, get_tasks_list_keyboard

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("notes"))
async def cmd_notes(message: types.Message):
    """Вывод списка последних заметок."""
    user_id = message.from_user.id
    notes = await get_user_notes(user_id=user_id, limit=10)

    if not notes:
        await message.answer("📝 У вас пока нет сохраненных заметок.\nНапишите текст или отправьте голосовое, чтобы создать первую!")
        return

    builder = InlineKeyboardBuilder()
    text_lines = ["📋 <b>Ваши последние заметки:</b>\n"]
    
    for idx, n in enumerate(notes, 1):
        dt_str = n.created_at.strftime("%d.%m %H:%M")
        title_snippet = n.title[:35]
        tags_str = f" [#{n.tags.replace(',', ' #')}]" if n.tags else ""
        text_lines.append(f"{idx}. <b>{title_snippet}</b> <i>({dt_str})</i>{tags_str}")
        
        builder.row(
            types.InlineKeyboardButton(
                text=f"🔍 {idx}. {title_snippet}",
                callback_data=f"view_note:{n.id}"
            )
        )

    await message.answer("\n".join(text_lines), parse_mode="HTML", reply_markup=builder.as_markup())


@router.message(Command("search"))
async def cmd_search(message: types.Message):
    """Поиск по тексту заметок."""
    query = message.text.replace("/search", "").strip()
    if not query:
        await message.answer("Использование: <code>/search &lt;поисковый запрос&gt;</code>\nНапример: <code>/search договор</code>", parse_mode="HTML")
        return

    user_id = message.from_user.id
    found_notes = await search_notes(user_id=user_id, query=query, limit=10)

    if not found_notes:
        await message.answer(f"🔍 По запросу «{query}» ничего не найдено.")
        return

    builder = InlineKeyboardBuilder()
    text_lines = [f"🔍 <b>Результаты поиска по запросу «{query}»:</b>\n"]

    for idx, n in enumerate(found_notes, 1):
        dt_str = n.created_at.strftime("%d.%m.%Y")
        text_lines.append(f"{idx}. <b>{n.title}</b> ({dt_str})")
        builder.row(
            types.InlineKeyboardButton(
                text=f"📖 {n.title[:30]}",
                callback_data=f"view_note:{n.id}"
            )
        )

    await message.answer("\n".join(text_lines), parse_mode="HTML", reply_markup=builder.as_markup())


@router.message(Command("ask"))
async def cmd_ask(message: types.Message):
    """Интеллектуальный вопрос ИИ по базе заметок (RAG)."""
    query = message.text.replace("/ask", "").strip()
    if not query:
        await message.answer(
            "Использование: <code>/ask &lt;ваш вопрос&gt;</code>\n"
            "Например: <code>/ask что я планировал на эту неделю по проекту?</code>",
            parse_mode="HTML"
        )
        return

    user_id = message.from_user.id
    status_msg = await message.reply("🧠 <i>Ищу информацию в ваших заметках и формулирую ответ...</i>", parse_mode="HTML")
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # 1. Ищем релевантные заметки по ключевым словам + берем свежие
    matched_notes = await search_notes(user_id=user_id, query=query, limit=8)
    recent_notes = await get_user_notes(user_id=user_id, limit=8)
    
    # Объединяем без дубликатов
    all_notes_dict = {n.id: n for n in (matched_notes + recent_notes)}
    notes_list = list(all_notes_dict.values())

    notes_context = [
        {
            "id": n.id,
            "created_at": n.created_at.strftime("%d.%m.%Y %H:%M"),
            "title": n.title,
            "tags": n.tags,
            "summary": n.summary,
            "raw_content": n.raw_content[:800]
        }
        for n in notes_list
    ]

    # Получаем активные задачи
    tasks = await get_user_tasks(user_id=user_id, limit=15)
    tasks_context = [
        {
            "title": t.title,
            "due_date": format_datetime_human(t.due_date),
            "status": t.status
        }
        for t in tasks
    ]

    try:
        answer, model_used = await ai_service.ask_notes_rag(
            user_query=query,
            notes_context=notes_context,
            tasks_context=tasks_context
        )
        reply_text = f"💡 <b>Ответ ассистента:</b>\n\n{answer}\n\n<i>🤖 Модель: {model_used}</i>"
        await status_msg.edit_text(reply_text, parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Ошибка при RAG-поиске: {e}")
        await status_msg.edit_text(f"⚠️ Не удалось получить ответ от ИИ: {e}")


@router.callback_query(F.data.startswith("view_note:"))
async def cb_view_note(callback: types.CallbackQuery):
    note_id = int(callback.data.split(":")[1])
    note = await get_note_by_id(note_id)

    if not note:
        await callback.answer("Заметка не найдена или удалена.", show_alert=True)
        return

    tags_line = f"\n📌 #{note.tags.replace(',', ' #')}" if note.tags else ""
    dt_str = note.created_at.strftime("%d.%m.%Y в %H:%M")

    msg_parts = [
        f"📝 <b>{note.title}</b> ({dt_str}){tags_line}\n"
    ]

    if note.summary:
        msg_parts.append(f"💡 <b>Главное:</b>\n{note.summary}\n")

    if note.timeline:
        msg_parts.append(f"⏳ <b>Хронология:</b>\n{note.timeline}\n")

    if note.tasks:
        msg_parts.append(f"⏰ <b>Задачи ({len(note.tasks)}):</b>")
        for idx, t in enumerate(note.tasks, 1):
            st = "✅" if t.status == "completed" else "📌"
            msg_parts.append(f"{idx}. {st} {t.title} (срок: {format_datetime_human(t.due_date)})")
        msg_parts.append("")

    raw_preview = note.raw_content if len(note.raw_content) <= 500 else note.raw_content[:500] + "..."
    msg_parts.append(f"📄 <b>Исходный текст:</b>\n<i>«{raw_preview}»</i>")

    keyboard = get_note_created_keyboard(note.id, note.tasks)
    await callback.message.answer("\n".join(msg_parts), parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("delete_note:"))
async def cb_delete_note(callback: types.CallbackQuery):
    note_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    deleted = await delete_note_by_id(note_id=note_id, user_id=user_id)
    if deleted:
        await callback.answer("Заметка успешно удалена.")
        await callback.message.edit_text("🗑 <b>Заметка и связанные задачи удалены.</b>", parse_mode="HTML")
    else:
        await callback.answer("Не удалось удалить заметку.", show_alert=True)


@router.callback_query(F.data.startswith("note_tasks_edit:"))
async def cb_note_tasks_edit(callback: types.CallbackQuery):
    """Показывает список задач заметки для выбора изменения времени."""
    note_id = int(callback.data.split(":")[1])
    note = await get_note_by_id(note_id)

    if not note or not note.tasks:
        await callback.answer("У этой заметки нет задач.", show_alert=True)
        return

    keyboard = get_tasks_list_keyboard(note.tasks)
    await callback.message.answer(
        "✏️ <b>Выберите задачу, чтобы изменить время или отменить напоминание:</b>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_note(message: types.Message):
    """
    Обработка любого обычного текстового сообщения.
    Структурирует текст с помощью ИИ, выделяет задачи и напоминания в UTC+5.
    """
    raw_text = message.text.strip()
    if not raw_text:
        return

    user_id = message.from_user.id
    status_msg = await message.reply("🤖 <i>Анализирую текст, структурирую и выделяю задачи...</i>", parse_mode="HTML")
    
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

        # 1. Структурирование через OpenRouter
        structured_data = await ai_service.structure_note_and_tasks(
            raw_text=raw_text,
            is_voice=False
        )

        title = structured_data.get("title", "Заметка")
        summary = structured_data.get("summary", "")
        timeline = structured_data.get("timeline")
        tasks_list = structured_data.get("tasks", [])
        tags = structured_data.get("tags", [])
        model_used = structured_data.get("model_used", "OpenRouter Free")

        # 2. Сохранение в БД
        note, created_tasks = await create_note_with_tasks(
            user_id=user_id,
            title=title,
            raw_content=raw_text,
            summary=summary,
            timeline=timeline,
            tags=tags,
            tasks_data=tasks_list
        )

        # 3. Красивый ответ
        tags_line = " ".join([f"#{t.strip()}" for t in tags if t.strip()])

        msg_parts = [
            f"📝 <b>{title}</b>",
        ]
        if tags_line:
            msg_parts.append(f"📌 {tags_line}")

        if summary:
            msg_parts.append(f"\n💡 <b>Главное:</b>\n{summary}")

        if timeline:
            msg_parts.append(f"\n⏳ <b>Хронология / Таймлайн:</b>\n{timeline}")

        if created_tasks:
            msg_parts.append(f"\n⏰ <b>Запланированные задачи ({len(created_tasks)}):</b>")
            for idx, task in enumerate(created_tasks, 1):
                due_info = format_datetime_human(task.due_date)
                remind_info = f", напомню {format_datetime_human(task.remind_at)}" if task.remind_at else ""
                msg_parts.append(f"{idx}. <b>{task.title}</b>\n   └ <i>Срок: {due_info}{remind_info}</i>")

        msg_parts.append(f"\n─────────────\n⚙️ <i>LLM: {model_used}</i>")

        reply_markup = get_note_created_keyboard(note.id, created_tasks)

        await status_msg.edit_text(
            "\n".join(msg_parts),
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.exception(f"Ошибка при обработке текстовой заметки: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка при обработке заметки: {e}")
