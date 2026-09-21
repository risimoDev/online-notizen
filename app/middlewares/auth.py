import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from app.config import settings

logger = logging.getLogger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        allowed_ids = settings.allowed_telegram_ids

        # Если список разрешенных ID не пуст, проверяем пользователя
        if allowed_ids and user_id not in allowed_ids:
            logger.warning(f"Неавторизованный доступ от Telegram ID: {user_id}")
            if isinstance(event, Message):
                await event.answer(
                    f"🔒 <b>Доступ ограничен</b>\n\n"
                    f"Ваш Telegram ID: <code>{user_id}</code>\n"
                    f"Для получения доступа укажите этот ID в параметре <code>ALLOWED_TELEGRAM_IDS</code> в файле .env бота.",
                    parse_mode="HTML"
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("🔒 Доступ ограничен. Ваш ID не в списке разрешенных.", show_alert=True)
            return

        return await handler(event, data)
