import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import FSInputFile

from app.services.backup_service import create_database_backup
from app.utils.date_utils import get_now_yekt

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("backup"))
async def cmd_backup(message: types.Message):
    """Мгновенное создание и отправка резервной копии базы данных."""
    status_msg = await message.reply("📦 <i>Формирую резервную копию базы данных...</i>", parse_mode="HTML")
    try:
        zip_path = create_database_backup()
        now_str = get_now_yekt().strftime("%d.%m.%Y %H:%M:%S")
        size_kb = round(zip_path.stat().st_size / 1024, 1)

        doc = FSInputFile(str(zip_path))
        caption = (
            f"💾 <b>Резервная копия базы данных MyZapis</b>\n\n"
            f"📅 Дата: {now_str} (YEKT, UTC+5)\n"
            f"📦 Размер: {size_kb} КБ\n"
            f"Содержит: все ваши заметки, задачи, контакты CRM и историю встреч."
        )

        await message.answer_document(document=doc, caption=caption, parse_mode="HTML")
        await status_msg.delete()
    except Exception as e:
        logger.exception(f"Ошибка при создании бэкапа по команде: {e}")
        await status_msg.edit_text(f"⚠️ Не удалось создать резервную копию: {e}")
