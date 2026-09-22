import logging
from typing import Dict, Any, List
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.enums import ChatAction
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.database.session import get_user_tasks, batch_update_task_timings
from app.services.openrouter_service import ai_service
from app.utils.date_utils import parse_datetime_yekt, get_now_yekt, format_datetime_human

logger = logging.getLogger(__name__)

router = Router()

# Кэш предложенных таймингов для быстрого применения по кнопке (user_id -> List[dict])
pending_day_plans: Dict[int, List[dict]] = {}


@router.message(Command("plan", "schedule"))
async def cmd_plan_day(message: types.Message):
    """Интеллектуальное планирование дня (AI Day Planner)."""
    user_id = message.from_user.id
    now = get_now_yekt()
    end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    tasks = await get_user_tasks(user_id=user_id, status="pending")
    if not tasks:
        await message.answer("🎉 У вас нет незавершенных задач для планирования. Отличный повод отдохнуть или поставить новые цели!")
        return

    status_msg = await message.reply("🧠 <i>Анализирую ваши задачи, приоритеты и составляю идеальный график дня...</i>", parse_mode="HTML")
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    tasks_payload = [
        {
            "id": t.id,
            "title": t.title,
            "priority": t.priority,
            "due_date": t.due_date.strftime("%Y-%m-%d %H:%M") if t.due_date else None
        }
        for t in tasks[:15]
    ]

    try:
        plan_data = await ai_service.plan_day_schedule(tasks=tasks_payload)
        overview = plan_data.get("overview", "")
        blocks = plan_data.get("schedule_blocks", [])
        raw_timings = plan_data.get("task_timings", [])

        # Формируем задачи к обновлению
        tasks_map = {t.id: t for t in tasks}
        valid_timings = []
        for item in raw_timings:
            tid = item.get("task_id")
            due_str = item.get("due_date")
            parsed_dt = parse_datetime_yekt(due_str)
            if tid in tasks_map and parsed_dt:
                valid_timings.append({
                    "task_id": tid,
                    "due_date": parsed_dt,
                    "time_label": item.get("time_label", parsed_dt.strftime("%H:%M"))
                })

        pending_day_plans[user_id] = valid_timings

        msg_lines = [
            f"📅 <b>Умный план на день ({now.strftime('%d.%m.%Y')}):</b>\n",
            f"<i>💡 {overview}</i>\n"
        ]

        for block in blocks:
            msg_lines.append(f"<b>{block.get('block_title')}</b>")
            if block.get("description"):
                msg_lines.append(f"<i>{block.get('description')}</i>")

            block_tids = block.get("task_ids", [])
            for tid in block_tids:
                if tid in tasks_map:
                    t = tasks_map[tid]
                    # Ищем назначенное время
                    time_label = ""
                    for tm in valid_timings:
                        if tm["task_id"] == tid:
                            time_label = f"[{tm['time_label']}] "
                            break
                    msg_lines.append(f"• {time_label}{t.title}")
            msg_lines.append("")

        builder = InlineKeyboardBuilder()
        if valid_timings:
            builder.row(
                types.InlineKeyboardButton(
                    text=f"✅ Применить расписание ({len(valid_timings)} задач)",
                    callback_data="apply_day_plan"
                )
            )
        builder.row(
            types.InlineKeyboardButton(
                text="🔄 Перегенерировать",
                callback_data="regenerate_day_plan"
            )
        )

        await status_msg.edit_text(
            "\n".join(msg_lines),
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )

    except Exception as e:
        logger.exception(f"Ошибка при планировании дня: {e}")
        await status_msg.edit_text(f"⚠️ Не удалось сформировать график дня: {e}")


@router.callback_query(F.data == "apply_day_plan")
async def cb_apply_day_plan(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    timings = pending_day_plans.get(user_id)

    if not timings:
        await callback.answer("План устарел. Запустите /plan снова.", show_alert=True)
        return

    updated_count = await batch_update_task_timings(user_id=user_id, task_timings=timings)
    pending_day_plans.pop(user_id, None)

    await callback.answer(f"Применено к {updated_count} задачам!")
    await callback.message.reply(
        f"✅ <b>Расписание успешно утверждено!</b>\n\n"
        f"Обновлено сроков и напоминаний: {updated_count}.\n"
        f"Вы можете посмотреть актуальный список по команде /today",
        parse_mode="HTML"
    )


@router.callback_query(F.data == "regenerate_day_plan")
async def cb_regenerate_day_plan(callback: types.CallbackQuery):
    await callback.answer("Пересчитываю график...")
    await cmd_plan_day(callback.message)
