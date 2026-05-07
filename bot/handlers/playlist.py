"""Хендлер приёма ссылки на плейлист VK и запуска обработки."""

import re

from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ParseMode

from core.cache_manager import CacheManager
from core.downloader import Downloader
from core.scraper.mp3party import Mp3PartyScraper
from core.vk_fetcher import VKFetcher
from services.processor import process_playlist, is_user_busy
from utils.helpers import parse_vk_playlist_url
from utils.logger import get_logger
from utils.storage import UserStorage

log = get_logger("playlist_handler")

router = Router()

# Зависимости — инжектятся при инициализации
_vk_fetcher: VKFetcher | None = None
_scraper: Mp3PartyScraper | None = None
_downloader: Downloader | None = None
_cache_mgr: CacheManager | None = None
_storage: UserStorage | None = None
_fuzzy_threshold: int = 85
_max_playlist_size: int = 100
_rate_limit: float = 2.0


def set_dependencies(
    vk_fetcher: VKFetcher,
    scraper: Mp3PartyScraper,
    downloader: Downloader,
    cache_mgr: CacheManager,
    storage: UserStorage,
    fuzzy_threshold: int = 85,
    max_playlist_size: int = 100,
    rate_limit: float = 2.0,
) -> None:
    """Устанавливает зависимости (вызывается при инициализации бота)."""
    global _vk_fetcher, _scraper, _downloader, _cache_mgr, _storage
    global _fuzzy_threshold, _max_playlist_size, _rate_limit
    _vk_fetcher = vk_fetcher
    _scraper = scraper
    _downloader = downloader
    _cache_mgr = cache_mgr
    _storage = storage
    _fuzzy_threshold = fuzzy_threshold
    _max_playlist_size = max_playlist_size
    _rate_limit = rate_limit


# Фильтр: ЛС + текст содержит vk.com
@router.message(F.chat.type == "private", F.text.contains("vk.com"))
async def handle_playlist_link(message: Message):
    """Обрабатывает сообщение с chat_id + ссылкой на плейлист VK."""
    text = message.text.strip()
    user_id = message.from_user.id

    # Проверяем зависимости
    if not all([_vk_fetcher, _scraper, _downloader, _cache_mgr, _storage]):
        await message.answer("⚠️ Бот не полностью инициализирован. Попробуйте позже.")
        return

    # Парсим: <chat_id> <url>
    parts = text.split(maxsplit=1)
    
    # Если только URL без chat_id — берём первый привязанный канал
    if len(parts) == 1:
        channels = _storage.get_channels(user_id)
        if not channels:
            await message.answer(
                "❌ Укажите chat_id канала перед ссылкой:\n"
                "<code>-100XXXXXXXXXX ссылка_на_плейлист</code>\n\n"
                "Или привяжите канал через /link",
                parse_mode=ParseMode.HTML,
            )
            return
        channel_id = channels[0]
        vk_url = parts[0]
    else:
        # Первая часть — chat_id, вторая — URL
        try:
            channel_id = int(parts[0])
        except ValueError:
            # Может быть URL первым — пробуем наоборот
            channels = _storage.get_channels(user_id)
            if not channels:
                await message.answer(
                    "❌ Неверный формат. Отправьте:\n"
                    "<code>-100XXXXXXXXXX ссылка_на_плейлист</code>",
                    parse_mode=ParseMode.HTML,
                )
                return
            channel_id = channels[0]
            vk_url = text
        else:
            vk_url = parts[1]

    # Валидация ссылки VK
    parsed = parse_vk_playlist_url(vk_url)
    if not parsed:
        await message.answer(
            "❌ Не удалось распознать ссылку на плейлист VK.\n\n"
            "Поддерживаемые форматы:\n"
            "• <code>https://vk.com/music/playlist/123_456</code>\n"
            "• <code>https://vk.com/music/playlist/123_456_accesskey</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    owner_id, playlist_id, access_key = parsed

    # Проверяем, не занят ли пользователь
    if is_user_busy(user_id):
        await message.answer("⏳ У вас уже есть активная задача. Дождитесь её завершения или проверьте /status.")
        return

    # Проверяем, что канал привязан к пользователю
    if not _storage.has_channel(user_id, channel_id):
        # Если канал не привязан, пробуем добавить (пользователь мог не делать /link)
        _storage.add_channel(user_id, channel_id)

    # Получаем треки из VK
    await message.answer("🔍 Получаю треки из плейлиста VK...")

    try:
        tracks = await _vk_fetcher.get_playlist_tracks_async(
            owner_id, playlist_id, access_key, count=_max_playlist_size,
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при получении плейлиста из VK: {str(e)[:200]}")
        return

    if not tracks:
        await message.answer(
            "❌ Плейлист пуст или недоступен.\n\n"
            "Убедитесь, что:\n"
            "• Ссылка верна\n"
            "• Плейлист публичный или у бота есть доступ",
        )
        return

    # Запускаем обработку
    task_id = await process_playlist(
        user_id=user_id,
        channel_id=channel_id,
        tracks=tracks,
        bot=message.bot,
        cache_mgr=_cache_mgr,
        scraper=_scraper,
        downloader=_downloader,
        fuzzy_threshold=_fuzzy_threshold,
        rate_limit=_rate_limit,
    )

    await message.answer(
        f"🚀 Задача создана!\n\n"
        f"📋 ID задачи: <code>{task_id}</code>\n"
        f"🎵 Треков: {len(tracks)}\n"
        f"📡 Канал: <code>{channel_id}</code>\n\n"
        f"Статус: /status <code>{task_id}</code>",
        parse_mode=ParseMode.HTML,
    )

    log.info(
        "playlist_task_created",
        task_id=task_id,
        user_id=user_id,
        channel_id=channel_id,
        tracks=len(tracks),
    )
