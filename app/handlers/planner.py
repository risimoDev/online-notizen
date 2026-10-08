import logging
from typing import Dict, Any, List, Optional
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.enums import ChatAction
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.database.session import get_user_tasks, batch_update_task_timings
from app.services.openrouter_service import ai_service
from app.utils.date_utils import parse_datetime_yekt, get_now_yekt
from app.utils.text import esc, send_long_html

logger = logging.getLogger(__name__)

router = Router()

# Кэш предложенных таймингов для быстрого применения по кнопке (user_id -> List[dict])
pending_day_plans: Dict[int, List[dict]] = {}


def _to_int(value: Any) -> Optional[int]:
    """ИИ иногда возвращает ID строкой ("3") — приводим к int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@router.message(Command("plan", "schedule"))
async def cmd_plan_day(message: types.Message):
    """Интеллектуальное планирование дня (AI Day Planner)."""
    await build_day_plan(message, message.from_user.id)


async def build_day_plan(message: types.Message, user_id: int):
    now = get_now_yekt()
    end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    # Планируем только то, что относится к сегодняшнему дню: задачи без срока, на сегодня и просроченные.
    # Иначе ИИ переносил на сегодня задачи с дедлайном через неделю, а кнопка «Применить» перезаписывала их сроки.
    tasks = [
        t for t in await get_user_tasks(user_id=user_id, status="pending")
        if t.due_date is None or t.due_date <= end_of_today
    ]
    if not tasks:
        await message.answer("🎉 На сегодня нет незавершенных задач для планирования. Отличный повод отдохнуть или поставить новые цели!")
        return

    status_msg = await message.reply("🧠 <i>Анализирую ваши задачи, приоритеты и составляю идеальный график дня...</i>", parse_mode="HTML")
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    tasks = tasks[:15]
    tasks_payload = [
        {
            "id": t.id,
            "title": t.title,
            "priority": t.priority,
            "due_date": t.due_date.strftime("%Y-%m-%d %H:%M") if t.due_date else None
        }
        for t in tasks
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
            if not isinstance(item, dict):
                continue
            tid = _to_int(item.get("task_id"))
            due_str = item.get("due_date")
            parsed_dt = parse_datetime_yekt(due_str) if isinstance(due_str, str) else None
            if tid in tasks_map and parsed_dt:
                valid_timings.append({
                    "task_id": tid,
                    "due_date": parsed_dt,
                    "time_label": str(item.get("time_label") or parsed_dt.strftime("%H:%M"))
                })

        pending_day_plans[user_id] = valid_timings
        timing_labels = {tm["task_id"]: tm["time_label"] for tm in valid_timings}

        msg_lines = [
            f"📅 <b>Умный план на день ({now.strftime('%d.%m.%Y')}):</b>\n",
            f"<i>💡 {esc(overview)}</i>\n"
        ]

        for block in blocks:
            if not isinstance(block, dict):
                continue
            msg_lines.append(f"<b>{esc(block.get('block_title') or 'Блок')}</b>")
            if block.get("description"):
                msg_lines.append(f"<i>{esc(block.get('description'))}</i>")

            block_tids = block.get("task_ids") or []
            if not isinstance(block_tids, list):
                block_tids = [block_tids]
            for raw_tid in block_tids:
                tid = _to_int(raw_tid)
                if tid in tasks_map:
                    label = timing_labels.get(tid)
                    time_label = f"[{esc(label)}] " if label else ""
                    msg_lines.append(f"• {time_label}{esc(tasks_map[tid].title)}")
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

        await send_long_html(message, "\n".join(msg_lines), reply_markup=builder.as_markup(), edit_message=status_msg)

    except Exception as e:
        logger.exception(f"Ошибка при планировании дня: {e}")
        await status_msg.edit_text(f"⚠️ Не удалось сформировать график дня: {esc(e)}")


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
    # callback.message отправлен ботом, поэтому его from_user — сам бот; берем пользователя из callback
    await build_day_plan(callback.message, callback.from_user.id)
