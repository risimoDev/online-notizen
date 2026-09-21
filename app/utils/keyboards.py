from typing import List, Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_note_created_keyboard(note_id: int, tasks: Optional[List] = None) -> InlineKeyboardMarkup:
    """Клавиатура под только что созданной заметкой."""
    builder = InlineKeyboardBuilder()

    if tasks:
        builder.row(
            InlineKeyboardButton(text="✏️ Изменить время задач", callback_data=f"note_tasks_edit:{note_id}")
        )
    
    builder.row(
        InlineKeyboardButton(text="🗑 Удалить заметку", callback_data=f"delete_note:{note_id}")
    )
    return builder.as_markup()


def get_task_item_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для карточки задачи."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Выполнено", callback_data=f"task_done:{task_id}"),
        InlineKeyboardButton(text="⏳ +1 час", callback_data=f"task_postpone:{task_id}:60")
    )
    builder.row(
        InlineKeyboardButton(text="📅 На завтра", callback_data=f"task_postpone_tomorrow:{task_id}"),
        InlineKeyboardButton(text="✏️ Время", callback_data=f"task_custom_time:{task_id}")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Удалить задачу", callback_data=f"task_delete:{task_id}")
    )
    return builder.as_markup()


def get_reminder_action_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Клавиатура, прикрепляемая к сообщению-напоминанию."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Выполнено", callback_data=f"task_done:{task_id}"),
        InlineKeyboardButton(text="⏳ +15 мин", callback_data=f"task_postpone:{task_id}:15")
    )
    builder.row(
        InlineKeyboardButton(text="⏳ +1 час", callback_data=f"task_postpone:{task_id}:60"),
        InlineKeyboardButton(text="📅 На завтра 10:00", callback_data=f"task_postpone_tomorrow:{task_id}")
    )
    return builder.as_markup()


def get_task_timing_presets_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Пресеты для быстрого изменения времени задачи."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⏳ Через 15 мин", callback_data=f"task_postpone:{task_id}:15"),
        InlineKeyboardButton(text="⏳ Через 1 час", callback_data=f"task_postpone:{task_id}:60")
    )
    builder.row(
        InlineKeyboardButton(text="Сегодня в 19:00", callback_data=f"task_set_fixed:{task_id}:today_19"),
        InlineKeyboardButton(text="Завтра в 10:00", callback_data=f"task_postpone_tomorrow:{task_id}")
    )
    builder.row(
        InlineKeyboardButton(text="Завтра в 15:00", callback_data=f"task_set_fixed:{task_id}:tomorrow_15"),
        InlineKeyboardButton(text="Без срока", callback_data=f"task_clear_time:{task_id}")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"task_view:{task_id}")
    )
    return builder.as_markup()


def get_tasks_list_keyboard(tasks: List) -> InlineKeyboardMarkup:
    """Клавиатура со списком задач для быстрого клика."""
    builder = InlineKeyboardBuilder()
    for task in tasks:
        status_icon = "✅" if task.status == "completed" else "📌"
        title_snippet = task.title[:30] + ("..." if len(task.title) > 30 else "")
        builder.row(
            InlineKeyboardButton(
                text=f"{status_icon} {title_snippet}",
                callback_data=f"task_view:{task.id}"
            )
        )
    return builder.as_markup()
