import os
import zipfile
import logging
from pathlib import Path
from datetime import datetime
from aiogram import Bot
from aiogram.types import FSInputFile

from app.config import settings
from app.utils.date_utils import get_now_yekt

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("data/backups")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def create_database_backup() -> Path:
    """Создает ZIP-архив файла базы данных SQLite с отметкой даты и времени."""
    db_path = Path(settings.db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Файл базы данных не найден: {db_path}")

    now = get_now_yekt()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    zip_filename = f"myzapis_backup_{timestamp}.zip"
    zip_path = BACKUP_DIR / zip_filename

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(db_path, arcname=db_path.name)

    logger.info(f"Резервная копия базы данных успешно создана: {zip_path} ({zip_path.stat().st_size} байт)")
    return zip_path


async def send_backup_to_users(bot: Bot):
    """Создает резервную копию и отправляет её всем разрешенным администраторам."""
    try:
        backup_path = create_database_backup()
        users = settings.allowed_telegram_ids
        now_str = get_now_yekt().strftime("%d.%m.%Y %H:%M")

        caption = (
            f"💾 <b>Автоматический бэкап базы данных</b>\n\n"
            f"📅 Дата: {now_str} (YEKT, UTC+5)\n"
            f"📦 Содержимое: все заметки, задачи, контакты CRM и история взаимодействий.\n\n"
            f"<i>Файл сохранен в надежном архиве.</i>"
        )

        for uid in users:
            try:
                document = FSInputFile(str(backup_path))
                await bot.send_document(
                    chat_id=uid,
                    document=document,
                    caption=caption,
                    parse_mode="HTML"
                )
                logger.info(f"Бэкап отправлен пользователю {uid}")
            except Exception as e:
                logger.error(f"Не удалось отправить бэкап пользователю {uid}: {e}")

    except Exception as e:
        logger.error(f"Ошибка при создании/отправке бэкапа базы данных: {e}")
