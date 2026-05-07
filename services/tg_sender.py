"""Отправка аудио в Telegram-канал с сохранением file_id."""

from typing import Optional

from aiogram import Bot
from aiogram.types import FSInputFile

from utils.helpers import Track
from utils.logger import get_logger

log = get_logger("tg_sender")


class TelegramSender:
    """Отправляет аудиофайлы в Telegram-канал."""

    def __init__(self, bot: Bot):
        self.bot = bot

    async def send_by_file_id(
        self,
        channel_id: int,
        file_id: str,
        track: Track,
    ) -> None:
        """Мгновенная отправка по file_id (без загрузки файла)."""
        try:
            await self.bot.send_audio(
                chat_id=channel_id,
                audio=file_id,
                performer=track.artist,
                title=track.title,
            )
            log.info(
                "sent_by_file_id",
                artist=track.artist,
                title=track.title,
                channel=channel_id,
            )
        except Exception as e:
            log.error(
                "send_file_id_error",
                error=str(e),
                artist=track.artist,
                title=track.title,
            )
            raise

    async def send_file(
        self,
        channel_id: int,
        file_path: str,
        track: Track,
    ) -> Optional[str]:
        """
        Отправляет mp3-файл в канал.
        
        Returns:
            file_id для кэширования или None при ошибке
        """
        try:
            audio_file = FSInputFile(file_path)
            result = await self.bot.send_audio(
                chat_id=channel_id,
                audio=audio_file,
                performer=track.artist,
                title=track.title,
                duration=track.duration if track.duration > 0 else None,
            )

            # Извлекаем file_id из ответа
            file_id = result.audio.file_id if result.audio else None

            log.info(
                "sent_file",
                artist=track.artist,
                title=track.title,
                channel=channel_id,
                file_id=file_id[:20] + "..." if file_id else None,
            )
            return file_id

        except Exception as e:
            log.error(
                "send_file_error",
                error=str(e),
                file_path=file_path,
                artist=track.artist,
                title=track.title,
            )
            raise
