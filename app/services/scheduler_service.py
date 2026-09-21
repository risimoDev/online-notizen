import logging
from datetime import datetime, timedelta
from typing import Optional
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot

from app.config import settings
from app.utils.date_utils import get_now_yekt, get_tz, format_datetime_human
from app.database.session import (
    get_due_reminders,
    mark_task_reminded,
    get_user_tasks
)
from app.utils.keyboards import get_reminder_action_keyboard

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=get_tz())
        self.bot: Optional[Bot] = None

    def setup(self, bot: Bot):
        self.bot = bot
        
        # 1. Проверка напоминаний каждую минуту
        self.scheduler.add_job(
            self.check_and_send_reminders,
            trigger=IntervalTrigger(seconds=30),
            id="check_reminders",
            replace_existing=True,
            misfire_grace_time=60
        )

        # 2. Утренний брифинг
        try:
            m_h, m_m = map(int, settings.morning_briefing_time.split(":"))
            self.scheduler.add_job(
                self.send_morning_briefing,
                trigger=CronTrigger(hour=m_h, minute=m_m, timezone=get_tz()),
                id="morning_briefing",
                replace_existing=True
            )
            logger.info(f"Утренний брифинг запланирован на {settings.morning_briefing_time} YEKT")
        except Exception as e:
            logger.warning(f"Не удалось запланировать утренний брифинг: {e}")

        # 3. Вечерний отчет
        try:
            e_h, e_m = map(int, settings.evening_briefing_time.split(":"))
            self.scheduler.add_job(
                self.send_evening_review,
                trigger=CronTrigger(hour=e_h, minute=e_m, timezone=get_tz()),
                id="evening_review",
                replace_existing=True
            )
            logger.info(f"Вечерний отчет запланирован на {settings.evening_briefing_time} YEKT")
        except Exception as e:
            logger.warning(f"Не удалось запланировать вечерний отчет: {e}")

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("APScheduler успешно запущен.")

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("APScheduler остановлен.")

    async def check_and_send_reminders(self):
        """Проверяет задачи, для которых наступило время напоминания."""
        if not self.bot:
            return

        now = get_now_yekt()
        due_tasks = await get_due_reminders(now)

        for task in due_tasks:
            try:
                text = (
                    f"⏰ <b>НАПОМИНАНИЕ!</b>\n\n"
                    f"📌 <b>Задача:</b> {task.title}\n"
                    f"⏳ <b>Срок:</b> {format_datetime_human(task.due_date)}\n"
                )
                if task.note:
                    text += f"📝 <i>Из заметки: {task.note.title}</i>\n"

                keyboard = get_reminder_action_keyboard(task.id)
                
                await self.bot.send_message(
                    chat_id=task.user_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
                await mark_task_reminded(task.id)
                logger.info(f"Отправлено напоминание для задачи #{task.id} пользователю {task.user_id}")
            except Exception as e:
                logger.error(f"Ошибка при отправке напоминания #{task.id}: {e}")

    async def send_morning_briefing(self):
        """Рассылка утреннего брифинга с задачами на сегодня."""
        if not self.bot:
            return

        users = settings.allowed_telegram_ids
        now = get_now_yekt()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, standing=False if hasattr(now, 'standing') else 59, microsecond=999999)

        for user_id in users:
            try:
                # Получаем активные задачи на сегодня + просроченные
                tasks = await get_user_tasks(user_id=user_id, status="pending", date_to=today_end)
                if not tasks:
                    continue

                msg_lines = [
                    f"☀️ <b>Доброе утро!</b>",
                    f"📅 Ваши задачи на сегодня ({now.strftime('%d.%m.%Y')}):\n"
                ]

                for i, t in enumerate(tasks, 1):
                    time_str = t.due_date.strftime("%H:%M") if t.due_date else "Без точного времени"
                    msg_lines.append(f"{i}. <b>[{time_str}]</b> {t.title}")

                msg_lines.append("\nЖелаю продуктивного дня! 💪\n/tasks — открыть управление задачами")
                
                await self.bot.send_message(
                    chat_id=user_id,
                    text="\n".join(msg_lines),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Ошибка утреннего брифинга для {user_id}: {e}")

    async def send_evening_review(self):
        """Вечерний отчет по выполненным и оставшимся задачам."""
        if not self.bot:
            return

        users = settings.allowed_telegram_ids
        now = get_now_yekt()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        for user_id in users:
            try:
                pending_tasks = await get_user_tasks(user_id=user_id, status="pending")
                overdue_tasks = [t for t in pending_tasks if t.due_date and t.due_date < now]
                
                msg_lines = [
                    f"🌙 <b>Итоги дня:</b>",
                ]

                if overdue_tasks:
                    msg_lines.append(f"\n⚠️ <b>Остались не завершены ({len(overdue_tasks)}):</b>")
                    for t in overdue_tasks[:5]:
                        msg_lines.append(f"• {t.title}")
                    msg_lines.append("\nВы можете перенести или закрыть их в списке: /tasks")
                else:
                    msg_lines.append("\n🎉 Все запланированные дела на сегодня закрыты! Отличная работа!")

                await self.bot.send_message(
                    chat_id=user_id,
                    text="\n".join(msg_lines),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Ошибка вечернего отчета для {user_id}: {e}")


scheduler_service = SchedulerService()
