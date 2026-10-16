"""Безопасные Telegram-операции: штатные ошибки («не изменено») не считаются сбоем."""

from typing import Optional, Sequence

from aiogram.types import Message, MessageEntity

from utils.logger import get_logger

log = get_logger("safe")

# ошибки, которые встречаются в штатной работе (гонки кликов, повтор, TTL)
_SILENT_PATTERNS = (
    "message is not modified",
    "message to edit not found",
    "message to delete not found",
    "message can't be edited",
    "message can't be deleted",
    "query is too old",
    "query id is invalid",
)


def is_silent(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(p in text for p in _SILENT_PATTERNS)


async def _edit(message: Message, text: str, *, parse_mode: Optional[str],
                entities: Optional[Sequence[MessageEntity]], reply_markup) -> bool:
    if message.photo or message.video or message.animation or message.document or message.audio:
        await message.edit_caption(
            caption=text, caption_entities=list(entities) if entities else None,
            parse_mode=parse_mode, reply_markup=reply_markup,
        )
    else:
        await message.edit_text(
            text, parse_mode=parse_mode, entities=list(entities) if entities else None,
            reply_markup=reply_markup,
        )
    return True


async def safe_edit(message: Message, text: str, *, parse_mode: Optional[str] = None,
                    entities: Optional[Sequence[MessageEntity]] = None,
                    reply_markup=None) -> bool:
    """Редактирует сообщение (фото→caption, текст→text). True — изменено.

    «message is not modified» и подобное — молча возвращаем False.
    """
    try:
        return await _edit(message, text, parse_mode=parse_mode, entities=entities, reply_markup=reply_markup)
    except Exception as e:
        if not is_silent(e):
            log.warning("safe_edit_failed", error=str(e))
        return False


async def edit_or_send(message: Message, text: str, *, parse_mode: Optional[str] = None,
                       entities: Optional[Sequence[MessageEntity]] = None,
                       reply_markup=None) -> None:
    """Редактирует сообщение; если по-настоящему не вышло — шлёт новое.

    В отличие от обычного fallback, дублей при «message is not modified» не создаёт.
    """
    try:
        await _edit(message, text, parse_mode=parse_mode, entities=entities, reply_markup=reply_markup)
    except Exception as e:
        if is_silent(e):
            return
        log.warning("edit_or_send_edit_failed", error=str(e))
        try:
            await message.answer(text, parse_mode=parse_mode, entities=list(entities) if entities else None,
                                 reply_markup=reply_markup)
        except Exception as e2:
            log.warning("edit_or_send_answer_failed", error=str(e2))
