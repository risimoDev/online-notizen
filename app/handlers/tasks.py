import logging
from datetime import datetime, timedelta
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.database.session import (
    get_user_tasks,
    get_task_by_id,
    update_task_status,
    update_task_timing,
    delete_task_by_id
)
from app.utils.date_utils import (
    get_now_yekt,
    format_datetime_human,
    calculate_remind_at
)
from app.utils.keyboards import (
    get_task_item_keyboard,
    get_task_timing_presets_keyboard
)

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("tasks"))
async def cmd_tasks(message: types.Message):
    """Вывод списка всех активных задач."""
    user_id = message.from_user.id
    tasks = await get_user_tasks(user_id=user_id, status="pending")

    if not tasks:
        await message.answer("🎉 У вас нет активных задач! Всё выполнено.")
        return

    builder = InlineKeyboardBuilder()
    text_lines = ["📋 <b>Ваши актуальные задачи:</b>\n"]

    for idx, t in enumerate(tasks, 1):
        due_str = format_datetime_human(t.due_date)
        text_lines.append(f"{idx}. <b>{t.title}</b>\n   └ <i>{due_str}</i>")
        builder.row(
            types.InlineKeyboardButton(
                text=f"⚙️ {idx}. {t.title[:25]}",
                callback_data=f"task_view:{t.id}"
            )
        )

    await message.answer(
        "\n".join(text_lines) + "\n\n<i>Нажмите на кнопку задачи для управления или отметки о выполнении:</i>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


@router.message(Command("today"))
async def cmd_today(message: types.Message):
    """Задачи на сегодня."""
    user_id = message.from_user.id
    now = get_now_yekt()
    end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    tasks = await get_user_tasks(user_id=user_id, status="pending", date_to=end_of_today)

    if not tasks:
        await message.answer("☕️ На сегодня задач нет. Можно отдохнуть или добавить новые!")
        return

    builder = InlineKeyboardBuilder()
    text_lines = [f"📅 <b>Задачи на сегодня ({now.strftime('%d.%m.%Y')}):</b>\n"]

    for idx, t in enumerate(tasks, 1):
        due_str = format_datetime_human(t.due_date)
        text_lines.append(f"{idx}. <b>{t.title}</b> ({due_str})")
        builder.row(
            types.InlineKeyboardButton(
                text=f"✅ {idx}. {t.title[:25]}",
                callback_data=f"task_view:{t.id}"
            )
        )

    await message.answer(
        "\n".join(text_lines),
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("task_view:"))
async def cb_task_view(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    task = await get_task_by_id(task_id)

    if not task or task.user_id != callback.from_user.id:
        await callback.answer("Задача не найдена или была удалена.", show_alert=True)
        return

    status_icon = "✅" if task.status == "completed" else "⏳"
    status_text = "Выполнена" if task.status == "completed" else "В процессе"
    due_str = format_datetime_human(task.due_date)
    remind_str = format_datetime_human(task.remind_at) if task.remind_at else "Не настроено"

    text_parts = [
        f"{status_icon} <b>Задача: {task.title}</b>\n",
        f"📊 <b>Статус:</b> {status_text}",
        f"📅 <b>Срок:</b> {due_str}",
        f"⏰ <b>Напоминание:</b> {remind_str}",
    ]
    if task.note:
        text_parts.append(f"📝 <b>Заметка:</b> {task.note.title}")

    keyboard = get_task_item_keyboard(task.id)
    await callback.message.edit_text("\n".join(text_parts), parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("task_done:"))
async def cb_task_done(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    task = await get_task_by_id(task_id)

    if not task or task.user_id != callback.from_user.id:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    await update_task_status(task_id=task_id, status="completed")
    await callback.answer("🎉 Отлично! Задача отмечена как выполненная.")
    await callback.message.edit_text(
        f"✅ <b>Задача выполнена:</b>\n<s>{task.title}</s>",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("task_postpone:"))
async def cb_task_postpone(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    minutes = int(parts[2])

    task = await get_task_by_id(task_id)
    if not task or task.user_id != callback.from_user.id:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    now = get_now_yekt()
    base_time = task.due_date if task.due_date and task.due_date > now else now
    new_due = base_time + timedelta(minutes=minutes)
    new_remind = calculate_remind_at(new_due)

    await update_task_timing(task_id=task_id, due_date=new_due, remind_at=new_remind)

    await callback.answer(f"⏳ Отложено на {minutes} минут.")
    await callback.message.edit_text(
        f"⏳ <b>Задача отложена:</b> {task.title}\n"
        f"📅 <b>Новый срок:</b> {format_datetime_human(new_due)}\n"
        f"⏰ <b>Напоминание:</b> {format_datetime_human(new_remind)}",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("task_postpone_tomorrow:"))
async def cb_task_postpone_tomorrow(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    task = await get_task_by_id(task_id)
    if not task or task.user_id != callback.from_user.id:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    now = get_now_yekt()
    # Завтра в 10:00 утра YEKT
    tomorrow_10am = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    new_remind = calculate_remind_at(tomorrow_10am)

    await update_task_timing(task_id=task_id, due_date=tomorrow_10am, remind_at=new_remind)

    await callback.answer("📅 Перенесено на завтра на 10:00.")
    await callback.message.edit_text(
        f"📅 <b>Задача перенесена на завтра:</b> {task.title}\n"
        f"⏰ <b>Новый срок:</b> {format_datetime_human(tomorrow_10am)}",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("task_custom_time:"))
async def cb_task_custom_time(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    task = await get_task_by_id(task_id)
    if not task or task.user_id != callback.from_user.id:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    keyboard = get_task_timing_presets_keyboard(task_id)
    await callback.message.edit_text(
        f"✏️ <b>Выберите новое время для задачи:</b>\n«{task.title}»",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task_set_fixed:"))
async def cb_task_set_fixed(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    preset = parts[2]

    task = await get_task_by_id(task_id)
    if not task or task.user_id != callback.from_user.id:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    now = get_now_yekt()

    if preset == "today_19":
        target_dt = now.replace(hour=19, minute=0, second=0, microsecond=0)
        if target_dt < now:
            target_dt += timedelta(days=1)
    elif preset == "tomorrow_15":
        target_dt = (now + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    else:
        target_dt = now + timedelta(hours=1)

    new_remind = calculate_remind_at(target_dt)
    await update_task_timing(task_id=task_id, due_date=target_dt, remind_at=new_remind)

    await callback.answer("Время успешно обновлено!")
    await callback.message.edit_text(
        f"⏰ <b>Время обновлено:</b> {task.title}\n"
        f"📅 <b>Срок:</b> {format_datetime_human(target_dt)}",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("task_clear_time:"))
async def cb_task_clear_time(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    task = await get_task_by_id(task_id)
    if not task or task.user_id != callback.from_user.id:
        await callback.answer("Задача не найдена.", show_alert=True)
        return
    await update_task_timing(task_id=task_id, due_date=None, remind_at=None)
    await callback.answer("Срок и напоминание удалены.")
    await callback.message.edit_text("⏳ Срок задачи снят (без срока).")


@router.callback_query(F.data.startswith("task_delete:"))
async def cb_task_delete(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    deleted = await delete_task_by_id(task_id=task_id, user_id=user_id)
    if deleted:
        await callback.answer("Задача удалена.")
        await callback.message.edit_text("🗑 <b>Задача удалена.</b>", parse_mode="HTML")
    else:
        await callback.answer("Не удалось удалить задачу.", show_alert=True)
