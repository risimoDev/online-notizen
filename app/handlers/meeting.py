import logging
from typing import Optional
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile

from app.services.openrouter_service import ai_service
from app.database.session import (
    create_note_with_tasks,
    upsert_contact_from_ai
)
from app.handlers.voice import extract_media, process_audio_file, transcript_file
from app.utils.date_utils import get_now_yekt, format_datetime_human
from app.utils.text import esc, send_long_html

logger = logging.getLogger(__name__)

router = Router()


def _split_participant(p: str):
    """'Иван (Team Lead)' -> ('Иван', 'Team Lead')."""
    if "(" in p:
        name, _, rest = p.partition("(")
        return name.strip(), rest.rstrip(")").strip() or None
    return p.strip(), None


async def process_meeting_transcript(
    message: types.Message,
    raw_text: str,
    stt_engine: Optional[str] = None,
    duration_seconds: Optional[int] = None
):
    user_id = message.from_user.id
    status_msg = await message.reply("📋 <i>Формирую официальный протокол совещания (Meeting Minutes)...</i>", parse_mode="HTML")
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    try:
        data = await ai_service.generate_meeting_protocol(raw_text=raw_text, duration_seconds=duration_seconds)

        topic = data.get("topic", "Совещание")
        participants = data.get("participants", [])
        timeline = data.get("timeline", [])
        decisions = data.get("decisions", [])
        action_items = data.get("action_items", [])
        unresolved = data.get("unresolved", [])
        model_used = data.get("model_used", "OpenRouter Free")

        now = get_now_yekt()
        now_str = now.strftime("%d.%m.%Y %H:%M")

        # 1. Формируем задачи
        tasks_data = []
        for item in action_items:
            tasks_data.append({
                "title": f"{item['task']} (Отв: {item['assignee']})",
                "due_date": item["due_date"],
                "priority": item["priority"]
            })

        timeline_str = "\n".join([f"• {t}" for t in timeline]) or None
        summary_str = "Решения:\n" + "\n".join([f"• {d}" for d in decisions]) if decisions else None

        # 2. Сохраняем заметку в БД
        note, created_tasks = await create_note_with_tasks(
            user_id=user_id,
            title=f"Протокол: {topic}",
            raw_content=raw_text,
            summary=summary_str,
            timeline=timeline_str,
            tags=["совещание", "встреча", "протокол"],
            audio_duration_seconds=duration_seconds,
            tasks_data=tasks_data
        )

        # 3. Сохраняем участников в CRM (привязываем к заметке протокола)
        for p in participants:
            name, role = _split_participant(p)
            try:
                await upsert_contact_from_ai(
                    user_id=user_id,
                    person_data={
                        "name": name,
                        "role": role,
                        "facts": f"Участвовал во встрече «{topic}»",
                        "agreements": None
                    },
                    note_id=note.id
                )
            except Exception as pe:
                logger.warning(f"Ошибка сохранения участника встречи в CRM: {pe}")

        # 4. Формируем сообщение в чат (все данные от ИИ экранируются)
        msg_parts = [
            "📋 <b>ПРОТОКОЛ ВСТРЕЧИ</b>",
            f"🎯 <b>Тема:</b> {esc(topic)}",
            f"📅 <b>Дата:</b> {now_str} (YEKT)\n"
        ]

        if participants:
            msg_parts.append(f"👥 <b>Участники:</b> {esc(', '.join(participants))}\n")

        if decisions:
            msg_parts.append("💡 <b>Принятые решения:</b>")
            for d in decisions:
                msg_parts.append(f"• {esc(d)}")
            msg_parts.append("")

        if action_items:
            msg_parts.append(f"⚡️ <b>Поручения ({len(action_items)}):</b>")
            for i, a in enumerate(action_items, 1):
                due_info = f" [до {format_datetime_human(a['due_date'])}]" if a["due_date"] else ""
                msg_parts.append(f"{i}. <b>{esc(a['task'])}</b> — <i>{esc(a['assignee'])}</i>{due_info}")
            msg_parts.append("")

        if unresolved:
            msg_parts.append("❓ <b>Открытые вопросы:</b>")
            for u in unresolved:
                msg_parts.append(f"• {esc(u)}")
            msg_parts.append("")

        footer = f"LLM: {esc(model_used)}"
        if stt_engine:
            footer = f"STT: {esc(stt_engine)} | {footer}"
        msg_parts.append(f"─────────────\n⚙️ <i>{footer}</i>")

        # 5. Генерируем Markdown файл для скачивания
        def md_due(a):
            return f", до {a['due_date'].strftime('%d.%m.%Y %H:%M')}" if a["due_date"] else ""

        md_file_content = f"""# Протокол встречи: {topic}
**Дата проведения:** {now_str}
**Участники:** {', '.join(participants) if participants else '—'}

---

## 💡 Принятые решения
{chr(10).join(['- ' + d for d in decisions]) if decisions else 'Не зафиксировано'}

---

## ⚡️ Поручения (Action Items)
{chr(10).join([f"- [ ] **{a['task']}** (Ответственный: {a['assignee']}{md_due(a)})" for a in action_items]) if action_items else 'Нет поручений'}

---

## ⏱ Хронология обсуждения
{chr(10).join(['- ' + t for t in timeline]) if timeline else '—'}

---

## ❓ Открытые вопросы
{chr(10).join(['- ' + u for u in unresolved]) if unresolved else 'Все вопросы согласованы'}

---
*Сгенерировано автоматически Telegram-ботом MyZapis*
"""
        safe_topic = "".join([c for c in topic if c.isalnum() or c in " _-"])[:25].strip() or "Meeting"
        doc_file = BufferedInputFile(md_file_content.encode("utf-8"), filename=f"Protocol_{safe_topic}.md")

        await send_long_html(message, "\n".join(msg_parts), edit_message=status_msg)
        await message.answer_document(
            document=doc_file,
            caption="📄 <b>Файл протокола встречи (.md)</b>\n<i>Вы можете переслать его коллегам или открыть в Obsidian/Notion.</i>",
            parse_mode="HTML"
        )
        if stt_engine:
            await message.answer_document(
                document=transcript_file(raw_text, f"Transcript_{safe_topic}"),
                caption="🗣 Полная расшифровка встречи"
            )

    except Exception as e:
        logger.exception(f"Ошибка при формировании протокола: {e}")
        try:
            await status_msg.edit_text(f"⚠️ Ошибка при формировании протокола: {esc(e)}")
        except Exception:
            pass


@router.message(Command("meeting"))
async def cmd_meeting(message: types.Message, command: CommandObject):
    """
    /meeting работает в нескольких режимах:
    - подпись /meeting к голосовому, аудио (.m4a, .mp3...), видео или аудиофайлу;
    - ответ /meeting на ранее отправленное аудио или текст;
    - /meeting <текст встречи>.
    Фильтр Command срабатывает и на подписи к медиа, поэтому message.text здесь может быть None.
    """
    media = extract_media(message)
    if media:
        await process_audio_file(message, media, meeting_mode=True)
        return

    text = (command.args or "").strip()
    if text:
        await process_meeting_transcript(message, text)
        return

    reply = message.reply_to_message
    if reply:
        reply_media = extract_media(reply)
        if reply_media:
            await process_audio_file(message, reply_media, meeting_mode=True)
            return
        reply_text = (reply.text or reply.caption or "").strip()
        if reply_text:
            await process_meeting_transcript(message, reply_text)
            return

    await message.answer(
        "🎙 <b>Режим протоколов совещаний (Meeting Minutes)</b>\n\n"
        "Как использовать:\n"
        "1. Отправьте команду вместе с текстом встречи:\n"
        "   <code>/meeting Обсудили запуск сайта. Иван показал дизайн...</code>\n"
        "2. Отправьте голосовое или аудиофайл созвона (.m4a, .mp3, .wav, до 20 МБ) с подписью <code>/meeting</code> или <code>#встреча</code>.\n"
        "3. Или ответьте <code>/meeting</code> на уже отправленную запись.\n\n"
        "ИИ автоматически выделит участников, решения, хронологию и поручения, а также создаст Markdown-документ!",
        parse_mode="HTML"
    )
