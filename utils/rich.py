"""Хелперы rich-сообщений (Bot API 10.x) с фолбэком на обычный текст/HTML."""

import time
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InputRichMessage, Message

from utils.logger import get_logger

log = get_logger("rich")

# Если rich-сообщения не поддержаны — не тратим round-trip на неудачный вызов
_rich_disabled_until = 0.0
_RICH_COOLDOWN = 600.0


def rich(md: str) -> InputRichMessage:
    return InputRichMessage(markdown=md)


async def send_rich(
    bot: Bot,
    chat_id: int,
    md: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    fallback: Optional[str] = None,
    parse_mode: str = "HTML",
) -> Optional[Message]:
    """Отправляет rich-сообщение; при неудаче — обычное (fallback).

    Возвращает отправленное сообщение (rich или fallback) либо None.
    """
    global _rich_disabled_until
    if time.time() >= _rich_disabled_until:
        try:
            return await bot.send_rich_message(chat_id=chat_id, rich_message=rich(md), reply_markup=reply_markup)
        except Exception as e:
            _rich_disabled_until = time.time() + _RICH_COOLDOWN
            log.warning("rich_send_failed", error=str(e))
    if fallback is not None:
        try:
            return await bot.send_message(chat_id, fallback, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception as e:
            log.warning("rich_fallback_send_failed", error=str(e))
    return None


async def edit_rich(
    bot: Bot,
    chat_id: int,
    message_id: int,
    md: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    fallback: Optional[str] = None,
    parse_mode: str = "HTML",
) -> bool:
    """Редактирует сообщение rich-контентом; при неудаче — обычным текстом."""
    global _rich_disabled_until
    if time.time() >= _rich_disabled_until:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                rich_message=rich(md), reply_markup=reply_markup,
            )
            return True
        except Exception as e:
            _rich_disabled_until = time.time() + _RICH_COOLDOWN
            log.warning("rich_edit_failed", error=str(e))
    if fallback is not None:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=fallback, parse_mode=parse_mode, reply_markup=reply_markup,
            )
        except Exception:
            pass
    return False
