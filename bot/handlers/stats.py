"""Хендлер /stats — статистика использования бота (для владельца)."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.processor import get_stats
from utils.rich import send_rich
from utils.storage import UserStorage

router = Router()

_storage: UserStorage | None = None
_owner_id: int = 0


def set_dependencies(storage: UserStorage, owner_id: int = 0) -> None:
    """Прокидывает зависимости из main."""
    global _storage, _owner_id
    _storage = storage
    _owner_id = owner_id


def _format_time(seconds: int) -> str:
    """Форматирует секунды в читаемый вид."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes}мин"
    return f"{minutes}мин"


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """/stats — показывает статистику использования бота (владельцу)."""
    if message.chat.type != "private":
        return

    uid = message.from_user.id if message.from_user else None
    owner = _owner_id or (_storage.get_owner() if _storage else None)
    if owner is None or uid != owner:
        await message.answer("ℹ️ Команда доступна владельцу.")
        return

    stats = get_stats()

    cache_rate = 0
    if stats["total_sent"] > 0:
        cache_rate = int(stats["total_cached"] / stats["total_sent"] * 100)

    md = (
        "# 📊 Статистика\n\n"
        "## 🎵 Обработано\n"
        f"- Плейлистов: **{stats['total_playlists']}**\n"
        f"- Треков: **{stats['total_tracks']}**\n\n"
        "## 📤 Отправлено\n"
        f"- Всего: **{stats['total_sent']}**\n"
        f"- Из кэша: {stats['total_cached']} ({cache_rate}%)\n"
        f"- Не найдено: {stats['total_not_found']}\n\n"
        "## ⏱ Общее время\n"
        f"{_format_time(stats['total_time'])}"
    )

    html = (
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

    await send_rich(message.bot, message.chat.id, md, fallback=html)
