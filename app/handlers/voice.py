import os
import html
import logging
from pathlib import Path
from typing import Optional
from aiogram import Router, types, F
from aiogram.enums import ChatAction

from app.services.stt_service import stt_service
from app.services.openrouter_service import ai_service
from app.database.session import create_note_with_tasks
from app.utils.date_utils import format_datetime_human
from app.utils.keyboards import get_note_created_keyboard

logger = logging.getLogger(__name__)

router = Router()

TEMP_DIR = Path("temp_audio")
TEMP_DIR.mkdir(parents=True, exist_ok=True)


async def process_audio_file(
    message: types.Message,
    file_id: str,
    file_ext: str,
    duration: Optional[int] = None
):
    user_id = message.from_user.id
    status_msg = await message.reply("🎙 <i>Слушаю и расшифровываю голосовое сообщение...</i>", parse_mode="HTML")
    
    temp_path = TEMP_DIR / f"{file_id}.{file_ext}"

    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        
        # 1. Скачиваем аудиофайл из Telegram
        file = await message.bot.get_file(file_id)
        await message.bot.download_file(file.file_path, destination=temp_path)

        # 2. Транскрибируем аудио
        raw_text, stt_engine = await stt_service.transcribe_audio(temp_path)

        if not raw_text or raw_text == "Не удалось разобрать речь в аудиосообщении.":
            await status_msg.edit_text("❌ Не удалось распознать речь в этом аудиосообщении.")
            return

        # Проверяем, не запрошен ли режим протокола встречи
        caption = (message.caption or "").lower()
        if "/meeting" in caption or "#встреча" in caption or "#совещание" in caption:
            await status_msg.delete()
            from app.handlers.meeting import process_meeting_transcript
            await process_meeting_transcript(message, raw_text)
            return

        # Обновляем статус
        await status_msg.edit_text("🤖 <i>Анализирую текст, хронологию и формирую задачи...</i>", parse_mode="HTML")
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

        # 3. ИИ-структурирование и выделение задач
        structured_data = await ai_service.structure_note_and_tasks(
            raw_text=raw_text,
            is_voice=True,
            audio_duration_seconds=duration
        )

        title = structured_data.get("title", "Голосовая заметка")
        summary = structured_data.get("summary", "")
        timeline = structured_data.get("timeline")
        tasks_list = structured_data.get("tasks", [])
        tags = structured_data.get("tags", [])
        people_list = structured_data.get("people", [])
        model_used = structured_data.get("model_used", "OpenRouter Free")

        # 4. Сохранение в базу данных (авто-режим)
        note, created_tasks = await create_note_with_tasks(
            user_id=user_id,
            title=title,
            raw_content=raw_text,
            summary=summary,
            timeline=timeline,
            tags=tags,
            audio_duration_seconds=duration,
            tasks_data=tasks_list
        )

        # 5. Сохранение людей в CRM
        for p in people_list:
            try:
                from app.database.session import upsert_contact_from_ai
                await upsert_contact_from_ai(user_id=user_id, person_data=p, note_id=note.id)
            except Exception as pe:
                logger.warning(f"Ошибка сохранения контакта в CRM: {pe}")

        # 6. Поиск связей (Serendipity Engine)
        matched_link = None
        try:
            from app.database.session import get_user_notes
            past_notes = await get_user_notes(user_id=user_id, limit=8)
            past_candidates = [
                {
                    "id": pn.id,
                    "title": pn.title,
                    "created_at": pn.created_at.strftime("%d.%m.%Y"),
                    "summary": pn.summary,
                    "raw_content": pn.raw_content
                }
                for pn in past_notes if pn.id != note.id
            ]
            if past_candidates:
                matched_link = await ai_service.find_serendipity_links(
                    new_title=title,
                    new_content=raw_text,
                    past_notes=past_candidates
                )
        except Exception as se:
            logger.warning(f"Ошибка Serendipity в voice: {se}")

        # 7. Формирование красивого ответа с экранированием HTML
        tags_line = " ".join([f"#{html.escape(str(t).strip())}" for t in tags if str(t).strip()])
        
        msg_parts = [
            f"📝 <b>{html.escape(title)}</b>",
        ]
        if tags_line:
            msg_parts.append(f"📌 {tags_line}")

        if summary:
            msg_parts.append(f"\n💡 <b>Главное:</b>\n{html.escape(summary)}")

        if timeline:
            msg_parts.append(f"\n⏳ <b>Хронология / Таймлайн:</b>\n{html.escape(timeline)}")

        if people_list:
            people_names = [html.escape(p["name"]) for p in people_list]
            msg_parts.append(f"\n👥 <b>Люди в CRM:</b> {', '.join(people_names)}")

        if created_tasks:
            msg_parts.append(f"\n⏰ <b>Запланированные задачи ({len(created_tasks)}):</b>")
            for idx, task in enumerate(created_tasks, 1):
                due_info = format_datetime_human(task.due_date)
                remind_info = f", напомню {format_datetime_human(task.remind_at)}" if task.remind_at else ""
                msg_parts.append(f"{idx}. <b>{html.escape(task.title)}</b>\n   └ <i>Срок: {due_info}{remind_info}</i>")

        if matched_link:
            msg_parts.append(
                f"\n🔮 <b>Неочевидная связь:</b>\n{html.escape(matched_link['insight'])}\n"
                f"<i>(перекликается с «{html.escape(matched_link['matched_note_title'])}»)</i>"
            )

        # Исходный текст голосового
        raw_preview = raw_text if len(raw_text) <= 500 else raw_text[:500] + "..."
        msg_parts.append(f"\n🗣 <b>Расшифровка речи:</b>\n<i>«{html.escape(raw_preview)}»</i>")

        # Инфо-подвал
        msg_parts.append(f"\n─────────────\n⚙️ <i>STT: {html.escape(stt_engine)} | LLM: {html.escape(model_used)}</i>")

        reply_markup = get_note_created_keyboard(note.id, created_tasks, matched_note=matched_link)

        try:
            await status_msg.edit_text(
                "\n".join(msg_parts),
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        except Exception:
            await status_msg.edit_text(
                "\n".join(msg_parts),
                parse_mode=None,
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.exception(f"Ошибка при обработке голосового сообщения: {e}")
        try:
            await status_msg.edit_text(f"⚠️ Произошла ошибка при обработке: {e}")
        except Exception:
            pass
    finally:
        # Удаляем временный файл
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


@router.message(F.voice)
async def handle_voice_message(message: types.Message):
    """Обработка стандартных голосовых сообщений."""
    voice = message.voice
    await process_audio_file(
        message=message,
        file_id=voice.file_id,
        file_ext="ogg",
        duration=voice.duration
    )


@router.message(F.audio)
async def handle_audio_message(message: types.Message):
    """Обработка переданных аудиозаписей (.mp3, .m4a, .wav и т.д.)."""
    audio = message.audio
    ext = audio.file_name.split(".")[-1] if audio.file_name and "." in audio.file_name else "mp3"
    await process_audio_file(
        message=message,
        file_id=audio.file_id,
        file_ext=ext,
        duration=audio.duration
    )


@router.message(F.video_note)
async def handle_video_note_message(message: types.Message):
    """Обработка видеосообщений (кружочков)."""
    vnote = message.video_note
    await process_audio_file(
        message=message,
        file_id=vnote.file_id,
        file_ext="mp4",
        duration=vnote.duration
    )
