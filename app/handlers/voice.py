import html
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from aiogram import Router, types, F
from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile

from app.services.stt_service import stt_service, STT_EMPTY_RESULT
from app.services.openrouter_service import ai_service
from app.database.session import (
    create_note_with_tasks,
    get_user_notes,
    upsert_contact_from_ai
)
from app.utils.date_utils import format_datetime_human, get_now_yekt
from app.utils.keyboards import get_note_created_keyboard
from app.utils.text import send_long_html

logger = logging.getLogger(__name__)

router = Router()

TEMP_DIR = Path("temp_audio")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Bot API позволяет боту скачивать файлы не больше 20 МБ
TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024
# Расшифровки длиннее этого порога дополнительно присылаются файлом
TRANSCRIPT_FILE_THRESHOLD = 1500

AUDIO_EXTENSIONS = {
    "mp3", "m4a", "wav", "ogg", "oga", "opus", "flac", "aac", "wma", "amr",
    "webm", "mp4", "mpeg", "mpga", "mkv", "mov"
}
MEETING_MARKERS = ("/meeting", "#встреча", "#совещание")


@dataclass
class MediaInfo:
    file_id: str
    ext: str
    duration: Optional[int]
    file_size: Optional[int]


def _ext_from_name(file_name: Optional[str], default: str) -> str:
    if file_name and "." in file_name:
        return file_name.rsplit(".", 1)[-1].lower()
    return default


def extract_media(message: types.Message) -> Optional[MediaInfo]:
    """
    Достает из сообщения голосовое, аудио, кружочек, видео или аудио/видео-файл,
    отправленный документом (так Telegram часто присылает .m4a/.wav).
    """
    if message.voice:
        v = message.voice
        return MediaInfo(v.file_id, "ogg", v.duration, v.file_size)
    if message.audio:
        a = message.audio
        return MediaInfo(a.file_id, _ext_from_name(a.file_name, "mp3"), a.duration, a.file_size)
    if message.video_note:
        v = message.video_note
        return MediaInfo(v.file_id, "mp4", v.duration, v.file_size)
    if message.video:
        v = message.video
        return MediaInfo(v.file_id, _ext_from_name(v.file_name, "mp4"), v.duration, v.file_size)
    if message.document:
        d = message.document
        mime = (d.mime_type or "").lower()
        ext = _ext_from_name(d.file_name, "")
        if mime.startswith(("audio/", "video/")) or ext in AUDIO_EXTENSIONS:
            return MediaInfo(d.file_id, ext or "bin", None, d.file_size)
    return None


def is_meeting_caption(message: types.Message) -> bool:
    caption = (message.caption or "").lower()
    return any(marker in caption for marker in MEETING_MARKERS)


def transcript_file(raw_text: str, title: str = "Transcript") -> BufferedInputFile:
    stamp = get_now_yekt().strftime("%Y%m%d_%H%M")
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-")[:30].strip() or "Transcript"
    return BufferedInputFile(raw_text.encode("utf-8"), filename=f"{safe_title}_{stamp}.txt")


async def process_audio_file(
    message: types.Message,
    media: MediaInfo,
    meeting_mode: bool = False
):
    user_id = message.from_user.id
    duration = media.duration

    if media.file_size and media.file_size > TELEGRAM_DOWNLOAD_LIMIT:
        size_mb = media.file_size / 1024 / 1024
        await message.reply(
            f"⚠️ Файл весит {size_mb:.1f} МБ, а Telegram разрешает ботам скачивать файлы не больше 20 МБ.\n\n"
            "Сожмите запись (в моно 32 кбит/с час разговора занимает ~15 МБ):\n"
            "<code>ffmpeg -i input.m4a -ac 1 -b:a 32k output.mp3</code>\n"
            "или разделите её на части.",
            parse_mode="HTML"
        )
        return

    long_hint = ""
    if (duration and duration > 300) or (media.file_size and media.file_size > 3 * 1024 * 1024):
        long_hint = "\n<i>Запись длинная — расшифровка может занять несколько минут.</i>"
    status_msg = await message.reply(f"🎙 <i>Слушаю и расшифровываю аудио...</i>{long_hint}", parse_mode="HTML")

    temp_path = TEMP_DIR / f"{uuid.uuid4().hex}.{media.ext}"

    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

        # 1. Скачиваем аудиофайл из Telegram
        file = await message.bot.get_file(media.file_id)
        await message.bot.download_file(file.file_path, destination=temp_path, timeout=300)

        # 2. Транскрибируем аудио
        raw_text, stt_engine = await stt_service.transcribe_audio(temp_path)

        if not raw_text or raw_text == STT_EMPTY_RESULT:
            await status_msg.edit_text("❌ Не удалось распознать речь в этом аудиосообщении.")
            return

        # Режим протокола встречи: команда /meeting или #встреча в подписи
        if meeting_mode or is_meeting_caption(message):
            await status_msg.delete()
            from app.handlers.meeting import process_meeting_transcript
            await process_meeting_transcript(
                message, raw_text, stt_engine=stt_engine, duration_seconds=duration
            )
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
                await upsert_contact_from_ai(user_id=user_id, person_data=p, note_id=note.id)
            except Exception as pe:
                logger.warning(f"Ошибка сохранения контакта в CRM: {pe}")

        # 6. Поиск связей (Serendipity Engine)
        matched_link = None
        try:
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
        await send_long_html(message, "\n".join(msg_parts), reply_markup=reply_markup, edit_message=status_msg)

        if len(raw_text) > TRANSCRIPT_FILE_THRESHOLD:
            await message.answer_document(
                document=transcript_file(raw_text, title),
                caption="🗣 Полная расшифровка"
            )

    except Exception as e:
        logger.exception(f"Ошибка при обработке голосового сообщения: {e}")
        try:
            await status_msg.edit_text(f"⚠️ Произошла ошибка при обработке: {html.escape(str(e))}")
        except Exception:
            pass
    finally:
        # Удаляем временный файл
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


@router.message(F.voice | F.audio | F.video_note | F.video | F.document)
async def handle_media_message(message: types.Message):
    """Голосовые, аудиофайлы (.mp3, .m4a, .wav...), кружочки, видео и аудио, присланные файлом."""
    media = extract_media(message)
    if media is None:
        await message.reply("📎 Я умею обрабатывать только аудио и видео. Текст можно просто отправить сообщением.")
        return
    await process_audio_file(message, media)
