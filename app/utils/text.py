import html
import logging
import re
from typing import List, Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Лимит Telegram — 4096 символов, оставляем запас
MESSAGE_LIMIT = 4000

_TAG_RE = re.compile(r"<[^>]+>")


def esc(value) -> str:
    """Экранирование любых пользовательских/LLM-данных перед вставкой в HTML-сообщение."""
    return html.escape(str(value)) if value is not None else ""


def html_to_plain(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text))


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> List[str]:
    """
    Делит текст на части не длиннее limit по границам строк.
    Каждая строка в наших сообщениях содержит сбалансированные теги, поэтому разрез
    по переводу строки не ломает HTML. Слишком длинная строка режется принудительно.
    """
    chunks: List[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks or [""]


async def _send_chunk(
    target: Message,
    chunk: str,
    edit: bool,
    reply_markup: Optional[InlineKeyboardMarkup]
) -> Message:
    method = target.edit_text if edit else target.answer
    try:
        return await method(chunk, parse_mode="HTML", reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "can't parse entities" not in str(e).lower():
            raise
        logger.warning(f"Не удалось разобрать HTML, отправляю простым текстом: {e}")
        return await method(html_to_plain(chunk), parse_mode=None, reply_markup=reply_markup)


async def send_long_html(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    edit_message: Optional[Message] = None
) -> Message:
    """
    Отправляет HTML-текст любой длины, разбивая его на несколько сообщений.
    Если передан edit_message, первая часть заменяет его текст (например, статус «Обрабатываю...»).
    Клавиатура прикрепляется к последней части.
    """
    chunks = split_message(text)
    last: Optional[Message] = None
    for idx, chunk in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        if idx == 0 and edit_message is not None:
            result = await _send_chunk(edit_message, chunk, edit=True, reply_markup=markup)
            last = result if isinstance(result, Message) else edit_message
        else:
            last = await _send_chunk(message, chunk, edit=False, reply_markup=markup)
    return last
