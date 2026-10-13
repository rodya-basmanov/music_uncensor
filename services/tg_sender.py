"""Отправка аудио в Telegram-канал с обложкой, троттлингом и retry на FloodWait."""

import asyncio
import os
import time
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import FSInputFile

from utils.helpers import Track
from utils.logger import get_logger

log = get_logger("tg_sender")

# Глобальный троттлинг отправок (на процесс): не чаще, чем раз в _send_interval
_send_lock = asyncio.Lock()
_last_send = 0.0
_send_interval = 3.0
_blocked_until = 0.0  # до какого времени все отправки ждут (после FloodWait)
_bot_username = ""


def set_bot_username(name: str) -> None:
    """Username самого бота — для подписи «via @...»."""
    global _bot_username
    _bot_username = (name or "").lstrip("@")


def bot_username() -> str:
    return _bot_username


def set_send_interval(seconds: float) -> None:
    """Минимальный интервал между отправками аудио (сек)."""
    global _send_interval
    try:
        _send_interval = max(0.0, float(seconds))
    except (TypeError, ValueError):
        _send_interval = 3.0


async def _throttle() -> None:
    """Выдерживает паузу между отправками (общая для всех воркеров)."""
    global _last_send
    async with _send_lock:
        now = time.time()
        wait = max(_send_interval - (now - _last_send), _blocked_until - now)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_send = time.time()


def _cover_input(cover):
    """Обложку можно передать только как локальный файл (aiogram требует InputFile)."""
    if cover and isinstance(cover, str) and os.path.exists(cover):
        return FSInputFile(cover)
    return None


class TelegramSender:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def _send_with_retry(self, make_call, max_retries: int = 6):
        """Вызывает make_call с троттлингом и retry на FloodWait/сетевые ошибки."""
        global _blocked_until
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            await _throttle()
            try:
                return await make_call()
            except TelegramRetryAfter as e:
                wait = int(e.retry_after) + 1
                last_exc = e
                # ставим общий стоп для всех воркеров
                _blocked_until = max(_blocked_until, time.time() + wait)
                log.warning("flood_wait", seconds=wait, attempt=attempt + 1)
                await asyncio.sleep(wait)
            except TelegramNetworkError as e:
                last_exc = e
                log.warning("send_network_error", error=str(e), attempt=attempt + 1)
                await asyncio.sleep(3)
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
            thumbnail=_cover_input(cover),
            reply_markup=reply_markup,
            caption=caption,
            parse_mode="HTML" if caption else None,
        ))
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
        ))
        file_id = result.audio.file_id if result.audio else None
        cover_file_id = ""
        if result.audio and result.audio.thumbnail:
            cover_file_id = result.audio.thumbnail.file_id
        log.info("sent_file", artist=track.artist, title=track.title, channel=channel_id)
        return {"file_id": file_id, "cover_file_id": cover_file_id, "message_id": result.message_id}
