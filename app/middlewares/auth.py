import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.filters import Filter
from aiogram.types import TelegramObject, Message, CallbackQuery

from app.services.access_service import access_service

logger = logging.getLogger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    """
    Пропускает только администраторов и добавленных ими пользователей.
    Регистрируется как outer-middleware, чтобы неавторизованные апдейты отсекались до фильтров.
    В data хендлера пробрасывается флаг is_admin.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user_id = None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            user_id = event.from_user.id

        if not access_service.is_allowed(user_id):
            logger.warning(f"Игнорирование сообщения от неавторизованного Telegram ID: {user_id}")
            # Полное игнорирование без отправки каких-либо сообщений
            return

        data["is_admin"] = access_service.is_admin(user_id)
        return await handler(event, data)


class AdminFilter(Filter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return access_service.is_admin(user.id if user else None)
