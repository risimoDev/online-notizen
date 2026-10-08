import logging
from typing import Optional
from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    KeyboardButton,
    KeyboardButtonRequestUsers,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.middlewares.auth import AdminFilter
from app.services.access_service import access_service
from app.database.session import get_bot_users
from app.utils.text import esc

logger = logging.getLogger(__name__)

router = Router()
# Все хендлеры роутера доступны только администраторам из .env
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

REQUEST_USERS_ID = 1


def _user_label(telegram_id: int, full_name: Optional[str], username: Optional[str]) -> str:
    parts = [esc(full_name) if full_name else "Без имени"]
    if username:
        parts.append(f"@{esc(username)}")
    parts.append(f"<code>{telegram_id}</code>")
    return " · ".join(parts)


async def _grant_access(
    message: types.Message,
    telegram_id: int,
    full_name: Optional[str] = None,
    username: Optional[str] = None
) -> str:
    if access_service.is_admin(telegram_id):
        return f"👑 {_user_label(telegram_id, full_name, username)} — уже администратор."

    _, created = await access_service.add_user(
        telegram_id=telegram_id,
        added_by=message.from_user.id,
        username=username,
        full_name=full_name
    )
    if not created:
        return f"ℹ️ {_user_label(telegram_id, full_name, username)} — уже имеет доступ."

    # Пытаемся уведомить пользователя (получится, только если он уже запускал бота)
    try:
        await message.bot.send_message(
            telegram_id,
            "✅ Вам открыт доступ к боту MyZapis.\nНажмите /start, чтобы начать."
        )
        notified = "пользователь уведомлён"
    except Exception:
        notified = "уведомить не удалось — попросите его открыть бота и нажать /start"
    return f"✅ Доступ выдан: {_user_label(telegram_id, full_name, username)}\n<i>({notified})</i>"


def _request_users_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(
                text="👤 Выбрать пользователя",
                request_users=KeyboardButtonRequestUsers(
                    request_id=REQUEST_USERS_ID,
                    user_is_bot=False,
                    max_quantity=10,
                    request_name=True,
                    request_username=True
                )
            )
        ], [KeyboardButton(text="Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


@router.message(Command("adduser"))
async def cmd_adduser(message: types.Message, command: CommandObject):
    """
    /adduser <telegram_id> [имя] — добавить по ID.
    /adduser в ответ на пересланное сообщение — добавить его автора.
    /adduser без аргументов — выбрать пользователя из контактов Telegram.
    """
    if access_service.open_mode:
        await message.answer("⚠️ Администраторы не заданы в .env — бот открыт для всех, управление пользователями отключено.")
        return

    args = (command.args or "").strip()
    if args:
        first, _, rest = args.partition(" ")
        if not first.lstrip("-").isdigit():
            await message.answer(
                "⚠️ Укажите числовой Telegram ID: <code>/adduser 123456789 Иван</code>\n"
                "Бот не может найти пользователя по @username — используйте /adduser без аргументов, "
                "чтобы выбрать человека из контактов.",
                parse_mode="HTML"
            )
            return
        text = await _grant_access(message, int(first), full_name=rest.strip() or None)
        await message.answer(text, parse_mode="HTML")
        return

    reply = message.reply_to_message
    origin = reply.forward_origin if reply else None
    if origin is not None:
        sender = getattr(origin, "sender_user", None)
        if sender is None:
            await message.answer("⚠️ Автор пересланного сообщения скрыл свой аккаунт. Используйте выбор из контактов: /adduser")
            return
        text = await _grant_access(message, sender.id, full_name=sender.full_name, username=sender.username)
        await message.answer(text, parse_mode="HTML")
        return

    await message.answer(
        "👤 <b>Добавление пользователя</b>\n\n"
        "Нажмите кнопку ниже и выберите одного или нескольких людей.\n"
        "Также можно: <code>/adduser 123456789 Имя</code> или ответить <code>/adduser</code> на пересланное от человека сообщение.",
        parse_mode="HTML",
        reply_markup=_request_users_keyboard()
    )


@router.message(F.users_shared)
async def handle_users_shared(message: types.Message):
    shared = message.users_shared
    if shared.request_id != REQUEST_USERS_ID:
        return

    lines = []
    for u in shared.users:
        full_name = " ".join(p for p in (u.first_name, u.last_name) if p) or None
        lines.append(await _grant_access(message, u.user_id, full_name=full_name, username=u.username))

    await message.answer("\n\n".join(lines), parse_mode="HTML", reply_markup=ReplyKeyboardRemove())


@router.message(F.text == "Отмена")
async def cancel_request_users(message: types.Message):
    await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())


@router.message(Command("deluser"))
async def cmd_deluser(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg.lstrip("-").isdigit():
        await message.answer(
            "Использование: <code>/deluser 123456789</code>\nИли откройте /users и нажмите на пользователя.",
            parse_mode="HTML"
        )
        return
    await _revoke(message, int(arg))


async def _revoke(message: types.Message, telegram_id: int):
    if access_service.is_admin(telegram_id):
        await message.answer("⚠️ Администратора можно убрать только из .env (ADMIN_TELEGRAM_IDS).")
        return
    removed = await access_service.remove_user(telegram_id)
    if removed:
        await message.answer(f"🚫 Доступ отозван у <code>{telegram_id}</code>. Его данные сохранены в базе.", parse_mode="HTML")
    else:
        await message.answer(f"ℹ️ Пользователь <code>{telegram_id}</code> не найден в списке.", parse_mode="HTML")


def _users_list_view(users) -> tuple:
    lines = ["👥 <b>Доступ к боту</b>\n", "👑 <b>Администраторы (.env):</b>"]
    for admin_id in sorted(access_service.admin_ids):
        lines.append(f"• <code>{admin_id}</code>")

    builder = InlineKeyboardBuilder()
    lines.append(f"\n👤 <b>Пользователи ({len(users)}):</b>")
    if not users:
        lines.append("<i>Пока никого. Добавьте командой /adduser</i>")
    for u in users:
        added = u.created_at.strftime("%d.%m.%Y")
        lines.append(f"• {_user_label(u.telegram_id, u.full_name, u.username)} <i>(с {added})</i>")
        title = u.full_name or (f"@{u.username}" if u.username else str(u.telegram_id))
        builder.row(types.InlineKeyboardButton(
            text=f"🚫 Отозвать: {title[:30]}",
            callback_data=f"user_revoke:{u.telegram_id}"
        ))
    return "\n".join(lines), builder.as_markup()


@router.message(Command("users"))
async def cmd_users(message: types.Message):
    users = await get_bot_users()
    text, markup = _users_list_view(users)
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data.startswith("user_revoke:"))
async def cb_user_revoke(callback: types.CallbackQuery):
    telegram_id = int(callback.data.split(":")[1])
    removed = await access_service.remove_user(telegram_id)
    await callback.answer("Доступ отозван." if removed else "Пользователь уже удалён.")
    users = await get_bot_users()
    text, markup = _users_list_view(users)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
