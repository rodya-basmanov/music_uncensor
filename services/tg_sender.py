"""Отправка аудио в Telegram-канал с обложкой, пер-чат троттлингом и retry."""

import asyncio
import os
import time
from collections import deque
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramEntityTooLarge,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import FSInputFile

from utils.helpers import Track
from utils.logger import get_logger

log = get_logger("tg_sender")

# Троттлинг на КАЖДЫЙ чат: не чаще _send_interval и не больше
# _CHAT_MAX_PER_WINDOW сообщений за окно (лимит Telegram ~20/мин на чат/канал).
_send_interval = 1.0
_CHAT_MAX_PER_WINDOW = 20
_CHAT_WINDOW = 60.0
_MAX_RETRIES = 5
_chat_locks: dict[int, asyncio.Lock] = {}
_chat_last: dict[int, float] = {}
_chat_times: dict[int, deque] = {}
_chat_blocked_until: dict[int, float] = {}  # per-chat пауза после FloodWait
_bot_username = ""


def set_bot_username(name: str) -> None:
    """Username самого бота — для подписи «via @...»."""
    global _bot_username
    _bot_username = (name or "").lstrip("@")


def bot_username() -> str:
    return _bot_username


def set_send_interval(seconds: float) -> None:
    """Минимальный интервал между отправками в один чат (сек)."""
    global _send_interval
    try:
        _send_interval = max(0.0, float(seconds))
    except (TypeError, ValueError):
        _send_interval = 1.0


def _chat_lock(chat_id: int) -> asyncio.Lock:
    lock = _chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _chat_locks[chat_id] = lock
    return lock


def _prune_chats() -> None:
    """Убирает данные неактивных чатов, чтобы словари не росли бесконечно."""
    if len(_chat_last) <= 1000:
        return
    now = time.time()
    for chat_id in [c for c, t in _chat_last.items() if now - t > 3600]:
        _chat_last.pop(chat_id, None)
        _chat_times.pop(chat_id, None)
        _chat_locks.pop(chat_id, None)
        _chat_blocked_until.pop(chat_id, None)


async def _throttle(chat_id: int) -> None:
    """Пейсит отправки в один чат: интервал + окно N/мин + пер-чат FloodWait."""
    _prune_chats()
    async with _chat_lock(chat_id):
        now = time.time()
        wait = _chat_blocked_until.get(chat_id, 0.0) - now
        if wait > 0:
            await asyncio.sleep(wait)
            now = time.time()
        spacing = _send_interval - (now - _chat_last.get(chat_id, 0.0))
        if spacing > 0:
            await asyncio.sleep(spacing)
            now = time.time()
        times = _chat_times.setdefault(chat_id, deque())
        while times and now - times[0] > _CHAT_WINDOW:
            times.popleft()
        if len(times) >= _CHAT_MAX_PER_WINDOW:
            await asyncio.sleep(_CHAT_WINDOW - (now - times[0]) + 0.05)
            now = time.time()
            while times and now - times[0] > _CHAT_WINDOW:
                times.popleft()
        times.append(now)
        _chat_last[chat_id] = now


def _block_chat(chat_id: int, seconds: float) -> None:
    """Ставит паузу только этому чату (FloodWait другого чата не блокирует всех)."""
    _chat_blocked_until[chat_id] = max(_chat_blocked_until.get(chat_id, 0.0), time.time() + seconds)


def _cover_input(cover):
    """Обложку можно передать только как локальный файл (aiogram требует InputFile)."""
    if cover and isinstance(cover, str) and os.path.exists(cover):
        return FSInputFile(cover)
    return None


class TelegramSender:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def _send_with_retry(self, make_call, chat_id: int, max_retries: int = _MAX_RETRIES):
        """Троттлинг + retry: FloodWait (пер-чат), 5xx и сетевые — с бэкоффом,
        «слишком большой файл» — без ретрая."""
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            await _throttle(chat_id)
            try:
                return await make_call()
            except TelegramRetryAfter as e:
                wait = int(e.retry_after) + 1
                last_exc = e
                _block_chat(chat_id, wait)
                log.warning("flood_wait", chat_id=chat_id, seconds=wait, attempt=attempt + 1)
                await asyncio.sleep(min(wait, 60))
            except TelegramEntityTooLarge as e:
                log.warning("entity_too_large", chat_id=chat_id, error=str(e))
                raise
            except TelegramServerError as e:
                last_exc = e
                backoff = min(2 ** attempt, 20)
                log.warning("send_server_error", chat_id=chat_id, attempt=attempt + 1, backoff=backoff)
                await asyncio.sleep(backoff)
            except TelegramNetworkError as e:
                last_exc = e
                backoff = min(2 ** attempt, 20)
                log.warning("send_network_error", chat_id=chat_id, attempt=attempt + 1, backoff=backoff)
                await asyncio.sleep(backoff)
        if last_exc:
            raise last_exc
        raise RuntimeError("send failed")

    async def send_by_file_id(self, channel_id: int, file_id: str, track: Track, cover=None, reply_markup=None, caption=None) -> dict:
        """Отправляет аудио по кэшированному file_id (без загрузки файла)."""
        result = await self._send_with_retry(lambda: self.bot.send_audio(
            chat_id=channel_id,
            audio=file_id,
            performer=track.artist or None,
            title=track.title or None,
            duration=track.duration if track.duration > 0 else None,
            thumbnail=_cover_input(cover),
            reply_markup=reply_markup,
            caption=caption,
            parse_mode="HTML" if caption else None,
        ), channel_id)
        cover_file_id = ""
        if result.audio and result.audio.thumbnail:
            cover_file_id = result.audio.thumbnail.file_id
        log.info("sent_by_file_id", artist=track.artist, title=track.title, channel=channel_id)
        return {"file_id": file_id, "cover_file_id": cover_file_id, "message_id": result.message_id}

    async def send_file(self, channel_id: int, file_path: str, track: Track, cover=None, reply_markup=None, caption=None) -> dict:
        """Загружает и отправляет mp3 с обложкой."""
        result = await self._send_with_retry(lambda: self.bot.send_audio(
            chat_id=channel_id,
            audio=FSInputFile(file_path),
            performer=track.artist or None,
            title=track.title or None,
            duration=track.duration if track.duration > 0 else None,
            thumbnail=_cover_input(cover),
            reply_markup=reply_markup,
            caption=caption,
            parse_mode="HTML" if caption else None,
        ), channel_id)
        file_id = result.audio.file_id if result.audio else None
        cover_file_id = ""
        if result.audio and result.audio.thumbnail:
            cover_file_id = result.audio.thumbnail.file_id
        log.info("sent_file", artist=track.artist, title=track.title, channel=channel_id)
        return {"file_id": file_id, "cover_file_id": cover_file_id, "message_id": result.message_id}
