import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import settings
from app.database.session import init_db
from app.middlewares.auth import WhitelistMiddleware
from app.handlers import common, voice, tasks, notes
from app.services.scheduler_service import scheduler_service

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)


async def main():
    logger.info("Запуск Telegram-бота myzapis...")

    # 1. Инициализация базы данных SQLite
    await init_db()
    logger.info(f"База данных успешно инициализирована ({settings.db_path}).")

    # 2. Инициализация бота и диспетчера
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # 3. Подключение Middleware авторизации
    whitelist_middleware = WhitelistMiddleware()
    dp.message.middleware(whitelist_middleware)
    dp.callback_query.middleware(whitelist_middleware)

    # 4. Регистрация роутеров
    # Порядок важен: common (команды) -> voice (аудио) -> tasks (команды задач) -> notes (текст и поиск)
    dp.include_router(common.router)
    dp.include_router(voice.router)
    dp.include_router(tasks.router)
    dp.include_router(notes.router)

    # 5. Инициализация и запуск планировщика задач (APScheduler)
    scheduler_service.setup(bot)
    scheduler_service.start()

    logger.info(f"Бот готов к работе. Часовой пояс: {settings.timezone} (UTC+5).")

    try:
        # Удаляем вебхук если был и сбрасываем старые апдейты
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка бота...")
        scheduler_service.shutdown()
        await bot.session.close()
        logger.info("Бот успешно остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Завершение процесса по сигналу прерывания.")
