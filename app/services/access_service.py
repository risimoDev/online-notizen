import logging
from typing import Optional, Set, Tuple

from app.config import settings
from app.database.models import BotUser
from app.database.session import get_bot_users, add_bot_user, remove_bot_user

logger = logging.getLogger(__name__)


class AccessService:
    """
    Права доступа к боту.
    - Администраторы задаются в .env (ADMIN_TELEGRAM_IDS / ALLOWED_TELEGRAM_IDS).
    - Обычных пользователей добавляют администраторы, они хранятся в таблице bot_users.
    Список пользователей кэшируется в памяти, чтобы middleware не ходил в БД на каждое сообщение.
    """

    def __init__(self):
        self._user_ids: Set[int] = set()

    async def load(self):
        users = await get_bot_users()
        self._user_ids = {u.telegram_id for u in users}
        if self.open_mode:
            logger.warning(
                "ADMIN_TELEGRAM_IDS не задан: бот доступен ВСЕМ пользователям Telegram, "
                "а управление пользователями отключено."
            )
        logger.info(f"Доступ: администраторов {len(settings.admin_ids)}, пользователей {len(self._user_ids)}.")

    @property
    def open_mode(self) -> bool:
        return not settings.admin_ids

    @property
    def admin_ids(self) -> Set[int]:
        return settings.admin_ids

    def is_admin(self, user_id: Optional[int]) -> bool:
        return user_id is not None and user_id in settings.admin_ids

    def is_allowed(self, user_id: Optional[int]) -> bool:
        if user_id is None:
            return False
        return self.open_mode or self.is_admin(user_id) or user_id in self._user_ids

    def recipient_ids(self) -> Set[int]:
        """Все, кому разрешено пользоваться ботом (для рассылок)."""
        return set(settings.admin_ids) | self._user_ids

    async def add_user(
        self,
        telegram_id: int,
        added_by: Optional[int] = None,
        username: Optional[str] = None,
        full_name: Optional[str] = None
    ) -> Tuple[BotUser, bool]:
        user, created = await add_bot_user(
            telegram_id=telegram_id,
            added_by=added_by,
            username=username,
            full_name=full_name
        )
        self._user_ids.add(telegram_id)
        logger.info(f"Пользователь {telegram_id} добавлен администратором {added_by} (новый: {created}).")
        return user, created

    async def remove_user(self, telegram_id: int) -> bool:
        removed = await remove_bot_user(telegram_id)
        self._user_ids.discard(telegram_id)
        if removed:
            logger.info(f"Пользователь {telegram_id} лишен доступа.")
        return removed


access_service = AccessService()
