import logging
import sqlite3
import zipfile
from pathlib import Path
from aiogram import Bot
from aiogram.types import FSInputFile

from app.config import settings
from app.utils.date_utils import get_now_yekt

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("data/backups")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
# Сколько последних архивов хранить на диске
BACKUPS_TO_KEEP = 10


def _rotate_backups():
    archives = sorted(BACKUP_DIR.glob("myzapis_backup_*.zip"))
    for old in archives[:-BACKUPS_TO_KEEP]:
        try:
            old.unlink()
        except Exception as e:
            logger.warning(f"Не удалось удалить старый бэкап {old}: {e}")


def create_database_backup() -> Path:
    """
    Создает ZIP-архив консистентной копии базы SQLite с отметкой даты и времени.
    Копия делается через SQLite Online Backup API: простое копирование файла во время
    записи в базу может дать поврежденный снимок.
    """
    db_path = Path(settings.db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Файл базы данных не найден: {db_path}")

    now = get_now_yekt()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    zip_path = BACKUP_DIR / f"myzapis_backup_{timestamp}.zip"
    snapshot_path = BACKUP_DIR / f"snapshot_{timestamp}.db"

    try:
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(snapshot_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(snapshot_path, arcname=db_path.name)
    finally:
        if snapshot_path.exists():
            snapshot_path.unlink()

    _rotate_backups()
    logger.info(f"Резервная копия базы данных успешно создана: {zip_path} ({zip_path.stat().st_size} байт)")
    return zip_path


async def send_backup_to_users(bot: Bot):
    """Создает резервную копию и отправляет её администраторам (в базе данные всех пользователей)."""
    try:
        backup_path = create_database_backup()
        admins = settings.admin_ids
        now_str = get_now_yekt().strftime("%d.%m.%Y %H:%M")

        caption = (
            f"💾 <b>Автоматический бэкап базы данных</b>\n\n"
            f"📅 Дата: {now_str} (YEKT, UTC+5)\n"
            f"📦 Содержимое: все заметки, задачи, контакты CRM и история взаимодействий.\n\n"
            f"<i>Файл сохранен в надежном архиве.</i>"
        )

        for uid in admins:
            try:
                document = FSInputFile(str(backup_path))
                await bot.send_document(
                    chat_id=uid,
                    document=document,
                    caption=caption,
                    parse_mode="HTML"
                )
                logger.info(f"Бэкап отправлен администратору {uid}")
            except Exception as e:
                logger.error(f"Не удалось отправить бэкап администратору {uid}: {e}")

    except Exception as e:
        logger.error(f"Ошибка при создании/отправке бэкапа базы данных: {e}")
