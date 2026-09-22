import logging
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.database.session import (
    get_user_contacts,
    get_contact_by_name,
    get_contact_by_id,
    delete_contact_by_id
)
from app.utils.date_utils import format_datetime_human

logger = logging.getLogger(__name__)

router = Router()


def get_contact_card_text(contact) -> str:
    """Форматирует досье контакта."""
    lines = [f"👤 <b>{contact.name}</b>"]
    if contact.role_or_company:
        lines.append(f"💼 <i>{contact.role_or_company}</i>")
    if contact.contact_info:
        lines.append(f"📞 Контакты: <code>{contact.contact_info}</code>")
    if contact.birthday:
        lines.append(f"🎂 День рождения: {contact.birthday}")

    last_dt_str = format_datetime_human(contact.last_interaction)
    lines.append(f"⏱ Последний контакт: {last_dt_str}\n")

    if contact.notes_summary:
        lines.append(f"💡 <b>Ключевые факты:</b>\n{contact.notes_summary}\n")

    if contact.agreements:
        lines.append(f"🤝 <b>Договоренности и обещания:</b>\n{contact.agreements}\n")

    return "\n".join(lines)


@router.message(Command("crm", "contacts"))
async def cmd_crm(message: types.Message):
    """Список всех контактов в персональной CRM."""
    user_id = message.from_user.id
    contacts = await get_user_contacts(user_id=user_id, limit=30)

    if not contacts:
        await message.answer(
            "👥 <b>Персональная CRM пуста.</b>\n\n"
            "Когда вы упоминаете людей в голосовых или тексте (например: <i>«Встретился с Андреем из Сбера, договорились списаться в пятницу»</i>), "
            "ИИ автоматически распознает их, соберет факты и внесет в CRM!",
            parse_mode="HTML"
        )
        return

    builder = InlineKeyboardBuilder()
    text_lines = [f"👥 <b>Ваша персональная CRM ({len(contacts)}):</b>\n"]

    for idx, c in enumerate(contacts, 1):
        role_part = f" ({c.role_or_company[:20]})" if c.role_or_company else ""
        text_lines.append(f"{idx}. <b>{c.name}</b>{role_part}")
        builder.row(
            types.InlineKeyboardButton(
                text=f"👤 {idx}. {c.name}{role_part}",
                callback_data=f"crm_view:{c.id}"
            )
        )

    await message.answer(
        "\n".join(text_lines) + "\n\n<i>Нажмите на контакт для просмотра досье и договоренностей:</i>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


@router.message(Command("whois"))
async def cmd_whois(message: types.Message):
    """Моментальное досье на человека: /whois <имя>."""
    name_query = message.text.replace("/whois", "").strip()
    if not name_query:
        await message.answer("Использование: <code>/whois &lt;имя&gt;</code>\nНапример: <code>/whois Андрей</code>", parse_mode="HTML")
        return

    user_id = message.from_user.id
    contact = await get_contact_by_name(user_id=user_id, query_name=name_query)

    if not contact:
        await message.answer(f"🔍 В вашей CRM нет информации о человеке «{name_query}».")
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="🗑 Удалить контакт", callback_data=f"crm_delete:{contact.id}")
    )

    card_text = get_contact_card_text(contact)
    await message.answer(card_text, parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("crm_view:"))
async def cb_crm_view(callback: types.CallbackQuery):
    cid = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    contact = await get_contact_by_id(contact_id=cid, user_id=user_id)

    if not contact or contact.user_id != user_id:
        await callback.answer("Контакт не найден.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="🗑 Удалить контакт", callback_data=f"crm_delete:{contact.id}"),
        types.InlineKeyboardButton(text="🔙 К списку", callback_data="crm_back_list")
    )

    card_text = get_contact_card_text(contact)
    await callback.message.edit_text(card_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "crm_back_list")
async def cb_crm_back_list(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    contacts = await get_user_contacts(user_id=user_id, limit=30)

    if not contacts:
        await callback.message.edit_text("👥 Контактов пока нет.", parse_mode="HTML")
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    text_lines = [f"👥 <b>Ваша персональная CRM ({len(contacts)}):</b>\n"]

    for idx, c in enumerate(contacts, 1):
        role_part = f" ({c.role_or_company[:20]})" if c.role_or_company else ""
        text_lines.append(f"{idx}. <b>{c.name}</b>{role_part}")
        builder.row(
            types.InlineKeyboardButton(
                text=f"👤 {idx}. {c.name}{role_part}",
                callback_data=f"crm_view:{c.id}"
            )
        )

    await callback.message.edit_text(
        "\n".join(text_lines) + "\n\n<i>Нажмите на контакт для просмотра досье:</i>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("crm_delete:"))
async def cb_crm_delete(callback: types.CallbackQuery):
    cid = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    deleted = await delete_contact_by_id(contact_id=cid, user_id=user_id)

    if deleted:
        await callback.answer("Контакт удален из CRM.")
        await callback.message.edit_text("🗑 <b>Контакт удален.</b>", parse_mode="HTML")
    else:
        await callback.answer("Не удалось удалить контакт.", show_alert=True)
