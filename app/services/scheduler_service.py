import logging
from datetime import datetime, timedelta
from typing import Optional
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from app.config import settings
from app.utils.date_utils import get_now_yekt, get_tz, format_datetime_human
from app.database.session import (
    get_due_reminders,
    mark_task_reminded,
    get_user_tasks,
    get_user_ids_with_pending_tasks
)
from app.services.access_service import access_service
from app.utils.keyboards import get_reminder_action_keyboard
from app.utils.text import esc, split_message

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
                misfire_grace_time=1800,  # не пропускать запуск, если бот был занят в назначенную секунду
                coalesce=True,
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
                misfire_grace_time=1800,  # не пропускать запуск, если бот был занят в назначенную секунду
                coalesce=True,
                replace_existing=True
            )
            logger.info(f"Вечерний отчет запланирован на {settings.evening_briefing_time} YEKT")
        except Exception as e:
            logger.warning(f"Не удалось запланировать вечерний отчет: {e}")

        # 4. Еженедельный бэкап базы данных (каждое воскресенье в 03:00 YEKT)
        try:
            self.scheduler.add_job(
                self.run_scheduled_backup,
                trigger=CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=get_tz()),
                id="weekly_backup",
                misfire_grace_time=1800,  # не пропускать запуск, если бот был занят в назначенную секунду
                coalesce=True,
                replace_existing=True
            )
            logger.info("Еженедельный бэкап базы данных запланирован на вс 03:00 YEKT")
        except Exception as e:
            logger.warning(f"Не удалось запланировать еженедельный бэкап: {e}")

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
            # Пользователь лишен доступа — не беспокоим его
            if not access_service.is_allowed(task.user_id):
                await mark_task_reminded(task.id)
                continue
            try:
                text = (
                    f"⏰ <b>НАПОМИНАНИЕ!</b>\n\n"
                    f"📌 <b>Задача:</b> {esc(task.title)}\n"
                    f"⏳ <b>Срок:</b> {format_datetime_human(task.due_date)}\n"
                )
                if task.note:
                    text += f"📝 <i>Из заметки: {esc(task.note.title)}</i>\n"

                keyboard = get_reminder_action_keyboard(task.id)
                
                await self.bot.send_message(
                    chat_id=task.user_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
                await mark_task_reminded(task.id)
                logger.info(f"Отправлено напоминание для задачи #{task.id} пользователю {task.user_id}")
            except TelegramForbiddenError:
                # Пользователь заблокировал бота — иначе бот повторял бы попытку каждые 30 секунд
                logger.warning(f"Пользователь {task.user_id} заблокировал бота, напоминание #{task.id} пропущено.")
                await mark_task_reminded(task.id)
            except Exception as e:
                logger.error(f"Ошибка при отправке напоминания #{task.id}: {e}")

    async def _briefing_recipients(self):
        """Пользователи с активными задачами, у которых есть доступ к боту."""
        user_ids = await get_user_ids_with_pending_tasks()
        return [uid for uid in user_ids if access_service.is_allowed(uid)]

    async def _send_html(self, user_id: int, text: str):
        for chunk in split_message(text):
            await self.bot.send_message(chat_id=user_id, text=chunk, parse_mode="HTML")

    async def send_morning_briefing(self):
        """Рассылка утреннего брифинга с задачами на сегодня."""
        if not self.bot:
            return

        now = get_now_yekt()
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        for user_id in await self._briefing_recipients():
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
                    if t.due_date and t.due_date < now:
                        time_str = f"просрочено, {t.due_date.strftime('%d.%m %H:%M')}"
                    msg_lines.append(f"{i}. <b>[{time_str}]</b> {esc(t.title)}")

                msg_lines.append("\nЖелаю продуктивного дня! 💪\n/tasks — открыть управление задачами")

                await self._send_html(user_id, "\n".join(msg_lines))
            except Exception as e:
                logger.error(f"Ошибка утреннего брифинга для {user_id}: {e}")

    async def send_evening_review(self):
        """Вечерний отчет по выполненным и оставшимся задачам."""
        if not self.bot:
            return

        now = get_now_yekt()
        users = set(await self._briefing_recipients()) | access_service.recipient_ids()

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
                        msg_lines.append(f"• {esc(t.title)}")
                    msg_lines.append("\nВы можете перенести или закрыть их в списке: /tasks")
                else:
                    msg_lines.append("\n🎉 Все запланированные дела на сегодня закрыты! Отличная работа!")

                await self._send_html(user_id, "\n".join(msg_lines))
            except Exception as e:
                logger.error(f"Ошибка вечернего отчета для {user_id}: {e}")

    async def run_scheduled_backup(self):
        """Запуск регулярного еженедельного бэкапа базы данных."""
        if not self.bot:
            return
        logger.info("Запуск еженедельного бэкапа базы данных...")
        try:
            from app.services.backup_service import send_backup_to_users
            await send_backup_to_users(self.bot)
        except Exception as e:
            logger.error(f"Ошибка при выполнении запланированного бэкапа: {e}")


scheduler_service = SchedulerService()
