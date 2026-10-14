"""Кастомные эмодзи РЗТ и билдер текста с Telegram-entities.

Кастомные эмодзи можно слать только через entities (custom_emoji), и только если
боту это разрешено (владелец с Telegram Premium или username на Fragment). Если
нет — Telegram молча игнорирует entity, поэтому мы это детектим и тогда показываем
красивые обычные Unicode-эмодзи.

Замечание: чтобы кастом сработал, entity должен оборачивать ровно alt-эмодзи.
"""

import asyncio
from typing import Any

from aiogram.enums import MessageEntityType
from aiogram.types import MessageEntity

# name -> custom_emoji_id
CUSTOM: dict[str, str] = {
    "rzt": "5433672115259720218",
    "silver": "5269444578884208871",
    "gold": "5269326316959715807",
    "emerald": "5269472161164184199",
    "sapphire": "5271667430618316169",
    "ruby": "5271971308144445093",
    "winner": "5240374423453743060",
    "place1": "5339273003291142186",
    "place2": "5337151834907768741",
    "place3": "5339399623221998889",
    "medal": "5339034005540991474",
}

# базовый эмодзи = alt кастомного (Telegram требует, чтобы entity оборачивал
# ровно alt-эмодзи, иначе кастомный игнорируется)
_ALT: dict[str, str] = {
    "rzt": "😎",
    "silver": "😜",
    "gold": "😏",
    "emerald": "🧐",
    "sapphire": "😳",
    "ruby": "🤫",
    "winner": "🌼",
    "place1": "🥇",
    "place2": "🥈",
    "place3": "🥉",
    "medal": "🏅",
}

# красивые обычные Unicode-эмодзи (когда кастом недоступен)
_NICE: dict[str, str] = {
    "rzt": "🎯",
    "silver": "🤍",
    "gold": "💛",
    "emerald": "💚",
    "sapphire": "💙",
    "ruby": "❤️",
    "winner": "🏆",
    "place1": "🥇",
    "place2": "🥈",
    "place3": "🥉",
    "medal": "🏅",
}

# включён ли кастом (детектится один раз)
custom_enabled = False
_detected = False
_lock = asyncio.Lock()

# подстрока в названии тира -> ключ эмодзи
_TIER_KEYS = {
    "серебрян": "silver",
    "золот": "gold",
    "изумрудн": "emerald",
    "сапфиров": "sapphire",
    "рубинов": "ruby",
}


def tier_key(name: str) -> str:
    """Ключ эмодзи по названию тира ('' если неизвестен)."""
    low = (name or "").lower()
    for sub, key in _TIER_KEYS.items():
        if sub in low:
            return key
    return ""


def result_key(result: str, winner: bool = False) -> str:
    """Ключ эмодзи по результату номинации."""
    if winner or "победит" in (result or "").lower():
        return "winner"
    low = (result or "").lower()
    if "3 место" in low:
        return "place3"
    if "2 место" in low:
        return "place2"
    if "1 место" in low:
        return "place1"
    return "medal"


def _u16len(text: str) -> int:
    """Длина строки в UTF-16 code units (формат offset'ов Telegram)."""
    return len(text.encode("utf-16-le")) // 2


def custom_emoji_enabled() -> bool:
    return custom_enabled


def set_custom_enabled(value: bool) -> None:
    """Ручное включение/выключение кастомных эмодзи (и отметка «задетекчено»)."""
    global custom_enabled, _detected
    custom_enabled = bool(value)
    _detected = True


async def _detect(bot: Any, chat_id: int) -> bool:
    """Шлёт эмодзи с custom_emoji-entity и проверяет, сохранил ли его сервер."""
    key = "rzt"
    ent = MessageEntity(
        type=MessageEntityType.CUSTOM_EMOJI, offset=0, length=_u16len(_ALT[key]),
        custom_emoji_id=CUSTOM[key],
    )
    try:
        msg = await bot.send_message(chat_id, _ALT[key], entities=[ent], disable_notification=True)
    except Exception:
        return False
    supported = any(
        getattr(e, "type", None) == MessageEntityType.CUSTOM_EMOJI for e in (msg.entities or [])
    )
    try:
        await bot.delete_message(chat_id, msg.message_id)
    except Exception:
        pass
    return supported


async def ensure_detected(bot: Any, chat_id: int) -> None:
    """Один раз определяет, разрешены ли боту кастомные эмодзи."""
    global custom_enabled, _detected
    async with _lock:
        if _detected:
            return
        try:
            custom_enabled = await _detect(bot, chat_id)
        except Exception:
            custom_enabled = False
        _detected = True


class TextBuilder:
    """Собирает (text, entities) с bold/italic/code/blockquote/custom-emoji."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._entities: list[MessageEntity] = []
        self._len = 0
        self._bq_start: int | None = None
        self._bq_expandable = False

    def add(
        self,
        text: str,
        *,
        bold: bool = False,
        italic: bool = False,
        code: bool = False,
        blockquote: bool = False,
        expandable: bool = False,
        emoji_key: str = "",
    ) -> "TextBuilder":
        custom_id = ""
        if emoji_key:
            if custom_enabled and CUSTOM.get(emoji_key):
                text = _ALT.get(emoji_key, "💎")
                custom_id = CUSTOM[emoji_key]
            else:
                text = _NICE.get(emoji_key, _ALT.get(emoji_key, "💎"))
        if not text:
            return self
        start = self._len
        self._parts.append(text)
        length = _u16len(text)
        self._len += length
        if custom_id:
            self._add(MessageEntityType.CUSTOM_EMOJI, start, length, custom_emoji_id=custom_id)
        if bold:
            self._add(MessageEntityType.BOLD, start, length)
        if italic:
            self._add(MessageEntityType.ITALIC, start, length)
        if code:
            self._add(MessageEntityType.CODE, start, length)
        if blockquote:
            t = MessageEntityType.EXPANDABLE_BLOCKQUOTE if expandable else MessageEntityType.BLOCKQUOTE
            self._add(t, start, length)
        return self

    def _add(self, type_, offset: int, length: int, **kw) -> None:
        self._entities.append(MessageEntity(type=type_, offset=offset, length=length, **kw))

    def open_blockquote(self, expandable: bool = False) -> None:
        self._bq_start = self._len
        self._bq_expandable = expandable

    def close_blockquote(self) -> None:
        if self._bq_start is None:
            return
        length = self._len - self._bq_start
        if length > 0:
            t = MessageEntityType.EXPANDABLE_BLOCKQUOTE if self._bq_expandable else MessageEntityType.BLOCKQUOTE
            self._add(t, self._bq_start, length)
        self._bq_start = None

    @property
    def length(self) -> int:
        return self._len

    def build(self) -> tuple[str, list[MessageEntity]]:
        return "".join(self._parts), list(self._entities)
