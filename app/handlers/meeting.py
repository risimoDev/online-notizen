import logging
import tempfile
from pathlib import Path
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.enums import ChatAction
from aiogram.types import FSInputFile, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.openrouter_service import ai_service
from app.database.session import (
    create_note_with_tasks,
    upsert_contact_from_ai
)
from app.utils.date_utils import get_now_yekt, parse_datetime_yekt, format_datetime_human

logger = logging.getLogger(__name__)

router = Router()


async def process_meeting_transcript(message: types.Message, raw_text: str):
    user_id = message.from_user.id
    status_msg = await message.reply("📋 <i>Формирую официальный протокол совещания (Meeting Minutes)...</i>", parse_mode="HTML")
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    try:
        data = await ai_service.generate_meeting_protocol(raw_text=raw_text)

        topic = data.get("topic", "Совещание")
        participants = data.get("participants", [])
        timeline = data.get("timeline", [])
        decisions = data.get("decisions", [])
        action_items = data.get("action_items", [])
        unresolved = data.get("unresolved", [])

        now = get_now_yekt()
        now_str = now.strftime("%d.%m.%Y %H:%M")

        # 1. Сохраняем участников в CRM
        for p in participants:
            name = p.split("(")[0].strip() if "(" in p else p.strip()
            role = p.split("(")[1].rstrip(")") if "(" in p else None
            await upsert_contact_from_ai(
                user_id=user_id,
                person_data={
                    "name": name,
                    "role": role,
                    "facts": f"Участвовал во встрече «{topic}»",
                    "agreements": None
                }
            )

        # 2. Формируем задачи
        tasks_data = []
        for item in action_items:
            task_title = f"{item.get('task', 'Поручение')} (Отв: {item.get('assignee', 'Не назначен')})"
            due_dt = parse_datetime_yekt(item.get("due_date"))
            tasks_data.append({
                "title": task_title,
                "due_date": due_dt,
                "priority": item.get("priority", "medium")
            })

        timeline_str = "\n".join([f"• {t}" for t in timeline])
        summary_str = f"Решения:\n" + "\n".join([f"• {d}" for d in decisions])

        # 3. Сохраняем заметку в БД
        note, created_tasks = await create_note_with_tasks(
            user_id=user_id,
            title=f"Протокол: {topic}",
            raw_content=raw_text,
            summary=summary_str,
            timeline=timeline_str,
            tags=["совещание", "встреча", "протокол"],
            tasks_data=tasks_data
        )

        # 4. Формируем красивое сообщение в чат
        msg_parts = [
            f"📋 <b>ПРОТОКОЛ ВСТРЕЧИ</b>",
            f"🎯 <b>Тема:</b> {topic}",
            f"📅 <b>Дата:</b> {now_str} (YEKT)\n"
        ]

        if participants:
            msg_parts.append(f"👥 <b>Участники:</b> {', '.join(participants)}\n")

        if decisions:
            msg_parts.append("💡 <b>Принятые решения:</b>")
            for d in decisions:
                msg_parts.append(f"• {d}")
            msg_parts.append("")

        if action_items:
            msg_parts.append(f"⚡️ <b>Поручения ({len(action_items)}):</b>")
            for i, a in enumerate(action_items, 1):
                due_info = f" [до {format_datetime_human(parse_datetime_yekt(a.get('due_date')))}]" if a.get("due_date") else ""
                msg_parts.append(f"{i}. <b>{a.get('task')}</b> — <i>{a.get('assignee')}</i>{due_info}")
            msg_parts.append("")

        if unresolved:
            msg_parts.append("❓ <b>Открытые вопросы:</b>")
            for u in unresolved:
                msg_parts.append(f"• {u}")
            msg_parts.append("")

        # 5. Генерируем Markdown файл для скачивания
        md_file_content = f"""# Протокол встречи: {topic}
**Дата проведения:** {now_str}
**Участники:** {', '.join(participants)}

---

## 💡 Принятые решения
{chr(10).join(['- ' + d for d in decisions]) if decisions else 'Не зафиксировано'}

---

## ⚡️ Поручения (Action Items)
{chr(10).join([f"- [ ] **{a.get('task')}** (Ответственный: {a.get('assignee')})" for a in action_items]) if action_items else 'Нет поручений'}

---

## ⏱ Хронология обсуждения
{chr(10).join(['- ' + t for t in timeline]) if timeline else '—'}

---

## ❓ Открытые вопросы
{chr(10).join(['- ' + u for u in unresolved]) if unresolved else 'Все вопросы согласованы'}

---
*Сгенерировано автоматически Telegram-ботом MyZapis*
"""
        file_bytes = md_file_content.encode("utf-8")
        safe_topic = "".join([c for c in topic if c.isalnum() or c in " _-"])[:25].strip()
        doc_file = BufferedInputFile(file_bytes, filename=f"Protocol_{safe_topic}.md")

        await status_msg.edit_text("\n".join(msg_parts), parse_mode="HTML")
        await message.answer_document(
            document=doc_file,
            caption="📄 <b>Файл протокола встречи (.md)</b>\n<i>Вы можете переслать его коллегам или открыть в Obsidian/Notion.</i>",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.exception(f"Ошибка при формировании протокола: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка при формировании протокола: {e}")


@router.message(Command("meeting"))
async def cmd_meeting(message: types.Message):
    """Команда /meeting: вызов протоколирования по переданному тексту."""
    text = message.text.replace("/meeting", "").strip()
    if not text:
        await message.answer(
            "🎙 <b>Режим протоколов совещаний (Meeting Minutes)</b>\n\n"
            "Как использовать:\n"
            "1. Отправьте команду вместе с текстом встречи:\n"
            "   <code>/meeting Обсудили запуск сайта. Иван показал дизайн...</code>\n"
            "2. Или отправьте голосовое/аудиофайл созвона с подписью <code>/meeting</code> или тегом <code>#встреча</code>.\n\n"
            "ИИ автоматически выделит участников, решения, хронологию и поручения, а также создаст Markdown-документ!",
            parse_mode="HTML"
        )
        return

    await process_meeting_transcript(message, text)
