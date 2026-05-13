"""Хендлер /stats — статистика использования бота."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode

from services.processor import get_stats

router = Router()


def _format_time(seconds: int) -> str:
    """Форматирует секунды в читаемый вид."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes}мин"
    return f"{minutes}мин"


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """/stats — показывает статистику использования бота."""
    if message.chat.type != "private":
        return

    stats = get_stats()

    # Вычисляем дополнительные метрики
    cache_rate = 0
    if stats["total_sent"] > 0:
        cache_rate = int(stats["total_cached"] / stats["total_sent"] * 100)

    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"🎵 <b>Обработано:</b>\n"
        f"   Плейлистов: {stats['total_playlists']}\n"
        f"   Треков: {stats['total_tracks']}\n\n"
        f"📤 <b>Отправлено:</b>\n"
        f"   Всего: {stats['total_sent']}\n"
        f"   Из кэша: {stats['total_cached']} ({cache_rate}%)\n"
        f"   Не найдено: {stats['total_not_found']}\n\n"
        f"⏱ <b>Общее время работы:</b>\n"
        f"   {_format_time(stats['total_time'])}"
    )

    await message.answer(text, parse_mode=ParseMode.HTML)