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
            logger.warning(f"Игнорирование сообщения от неавторизованного Telegram ID: {user_id}")
            # Полное игнорирование без отправки каких-либо сообщений
            return

        return await handler(event, data)
