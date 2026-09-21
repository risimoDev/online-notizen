from aiogram import Router, types
from aiogram.filters import CommandStart, Command

from app.config import settings
from app.utils.date_utils import get_now_yekt
from app.services.openrouter_service import ai_service
from app.database.session import get_user_tasks, get_user_notes

router = Router()


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    user_name = message.from_user.first_name or "друг"
    now = get_now_yekt()
    now_str = now.strftime("%d.%m.%Y %H:%M")

    text = (
        f"👋 Привет, {user_name}!\n\n"
        f"Я твой <b>персональный ИИ-ассистент по заметкам и задачам</b>.\n\n"
        f"<b>Что я умею:</b>\n"
        f"🎙 <b>Голосовые сообщения:</b> отправляй любые голосовые любой длины. Я расшифрую их, выделю суть, разобью хронологию мыслей и извлеку задачи.\n"
        f"📝 <b>Текстовые заметки:</b> пиши любые мысли или планы обычным языком.\n"
        f"⏰ <b>Умные напоминания (UTC+5):</b> я сам понимаю «завтра в 3 часа», «через 2 часа», «в пятницу утром» и вовремя пришлю напоминание с кнопками управления.\n"
        f"🔍 <b>AI-поиск по базе:</b> спроси меня напрямую <code>/ask что я записывал про налоги?</code> или используй <code>/search</code>.\n\n"
        f"🕒 <b>Текущее время (Екатеринбург, YEKT):</b> {now_str}\n\n"
        f"📌 <b>Основные команды:</b>\n"
        f"• /tasks или /today — список актуальных задач\n"
        f"• /notes — последние заметки\n"
        f"• /search &lt;запрос&gt; — поиск заметок\n"
        f"• /ask &lt;вопрос&gt; — умный ответ ИИ по твоим записям\n"
        f"• /status — статус системы и модели\n\n"
        f"<i>Просто запиши голосовое или напиши текст прямо сюда!</i>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        f"📖 <b>Справка по командам:</b>\n\n"
        f"🔹 <b>Создание заметок и задач:</b>\n"
        f"Просто отправь текст или голосовое сообщение (или перешли из другого чата).\n"
        f"ИИ автоматически:\n"
        f"1. Структурирует информацию.\n"
        f"2. При необходимости строит хронологический таймлайн.\n"
        f"3. Выделит задачи и дедлайны.\n"
        f"4. Поставит напоминание в часовом поясе Екатеринбурга (UTC+5).\n\n"
        f"🔹 <b>Управление:</b>\n"
        f"• <code>/today</code> — задачи на сегодня\n"
        f"• <code>/tasks</code> — все незавершенные задачи\n"
        f"• <code>/notes</code> — список последних заметок\n"
        f"• <code>/search &lt;текст&gt;</code> — поиск по тексту и тегам\n"
        f"• <code>/ask &lt;вопрос&gt;</code> — интеллектуальный поиск (RAG)\n"
        f"• <code>/status</code> — информация о сервере, времени и моделях"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    user_id = message.from_user.id
    now = get_now_yekt()
    
    # Статистика из БД
    tasks = await get_user_tasks(user_id=user_id, status="pending")
    notes = await get_user_notes(user_id=user_id, limit=5)
    
    # Информация об ИИ моделях
    models = await ai_service.get_ordered_free_models()
    active_model = models[0] if models else "нет доступных"
    
    stt_info = "Groq Whisper-large-v3" if settings.groq_api_key else f"faster-whisper ({settings.local_whisper_model})"

    text = (
        f"⚙️ <b>Статус системы:</b>\n\n"
        f"🕒 <b>Серверное время (YEKT):</b> {now.strftime('%d.%m.%Y %H:%M:%S')} (UTC+5)\n"
        f"🤖 <b>Текущая бесплатная ИИ-модель:</b> <code>{active_model}</code>\n"
        f"🔄 <b>Всего моделей в ротации:</b> {len(models)}\n"
        f"🎙 <b>Движок распознавания речи:</b> {stt_info}\n"
        f"🔔 <b>Упреждение напоминаний:</b> за {settings.default_reminder_lead_minutes} мин\n"
        f"☀️ <b>Утренний брифинг:</b> {settings.morning_briefing_time} YEKT\n"
        f"🌙 <b>Вечерний отчет:</b> {settings.evening_briefing_time} YEKT\n\n"
        f"📊 <b>Ваша статистика:</b>\n"
        f"• Активных задач: {len(tasks)}\n"
        f"• Заметок: {len(notes)}"
    )
    await message.answer(text, parse_mode="HTML")
