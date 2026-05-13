"""Хендлер приёма плейлиста (.txt или текст) и запуска обработки.

Формат:
Название плейлиста (опционально, первая строка если не chat_id)
-chat_id (опционально, если указан - используется для отправки)
Артист - Название (каждый трек с новой строки)
Источник: script.js → VK Music → .txt → бот."""

import os
import tempfile

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from dataclasses import dataclass

from core.cache_manager import CacheManager
from core.downloader import Downloader
from core.scraper.mp3party import Mp3PartyScraper
from services.processor import process_playlist, is_user_busy
from utils.helpers import Track, parse_track_line
from utils.logger import get_logger
from utils.storage import UserStorage

log = get_logger("playlist_handler")

router = Router()

_scraper: Mp3PartyScraper | None = None
_downloader: Downloader | None = None
_cache_mgr: CacheManager | None = None
_storage: UserStorage | None = None
_fuzzy_threshold: int = 85
_max_playlist_size: int = 100
_rate_limit: float = 2.0


@dataclass
class ParsedPlaylist:
    """Результат парсинга плейлиста."""
    name: str | None  # Название плейлиста
    channel_id: int | None  # Chat ID канала
    tracks: list[Track]  # Список треков
    total_duration: int  # Общая длительность в секундах


def set_dependencies(
    scraper: Mp3PartyScraper,
    downloader: Downloader,
    cache_mgr: CacheManager,
    storage: UserStorage,
    fuzzy_threshold: int = 85,
    max_playlist_size: int = 100,
    rate_limit: float = 2.0,
) -> None:
    """Устанавливает зависимости (вызывается при инициализации бота)."""
    global _scraper, _downloader, _cache_mgr, _storage
    global _fuzzy_threshold, _max_playlist_size, _rate_limit
    _scraper = scraper
    _downloader = downloader
    _cache_mgr = cache_mgr
    _storage = storage
    _fuzzy_threshold = fuzzy_threshold
    _max_playlist_size = max_playlist_size
    _rate_limit = rate_limit


def _format_duration(seconds: int) -> str:
    """Форматирует секунды в читаемый вид: 5ч 30мин."""
    if seconds < 60:
        return f"{seconds} сек"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes}мин"
    return f"{minutes}мин"


def _parse_playlist(text: str) -> ParsedPlaylist:
    """Парсит текст плейлиста: название, chat_id, треки."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    playlist_name: str | None = None
    channel_id: int | None = None
    tracks: list[Track] = []

    # Анализируем первые строки
    for i, line in enumerate(lines):
        # Если строка похожа на chat_id (начинается с - и число)
        if line.lstrip("-").isdigit() and len(line) > 3:
            try:
                cid = int(line)
                if abs(cid) >= 100:
                    channel_id = cid
                    continue
            except ValueError:
                pass

        # Если не chat_id и не трек - это название плейлиста
        if i == 0 or (i == 1 and channel_id):
            # Проверяем что это не трек (треки имеют формат "Artist - Title")
            if " - " not in line:
                playlist_name = line
                continue

        # Парсим как трек
        track = parse_track_line(line)
        if track:
            tracks.append(track)

    # Вычисляем общую длительность
    total_duration = sum(t.duration for t in tracks if t.duration > 0)

    return ParsedPlaylist(
        name=playlist_name,
        channel_id=channel_id,
        tracks=tracks,
        total_duration=total_duration,
    )


def _parse_tracks(text: str) -> list[Track]:
    """Парсит строки формата Title - Artist в список Track (для обратной совместимости)."""
    tracks: list[Track] = []
    for line in text.splitlines():
        track = parse_track_line(line)
        if track:
            tracks.append(track)
    return tracks


def _is_valid_channel_id(channel_id: int) -> bool:
    """Проверяет, является ли ID валидным Telegram chat ID."""
    return abs(channel_id) >= 100


def _resolve_channel_id(text: str | None, user_id: int) -> int | None:
    """Определяет channel_id: из hint в тексте, иначе из default_channel."""
    # 1. Если в тексте указан chat_id — используем его
    if text:
        parts = text.strip().split(maxsplit=1)
        if parts:
            try:
                cid = int(parts[0])
                if _is_valid_channel_id(cid):
                    return cid
            except ValueError:
                pass
    # 2. Канал по умолчанию из storage
    default = _storage.get_default_channel(user_id)
    if default and _is_valid_channel_id(default):
        return default
    return None


async def _start_processing(message: Message, raw_text: str, channel_hint: str | None = None):
    """Общий цикл: парсинг треков, определение канала, запуск задачи."""
    user_id = message.from_user.id

    # Парсим плейлист (название, chat_id, треки, длительность)
    parsed = _parse_playlist(raw_text)

    if not parsed.tracks:
        await message.answer(
            "❌ <b>Треки не найдены</b>\n\n"
            "Проверьте формат: <code>Исполнитель - Название</code>\n"
            "Каждая строка — один трек.",
            parse_mode=ParseMode.HTML,
        )
        return

    tracks = parsed.tracks

    if len(tracks) > _max_playlist_size:
        tracks = tracks[:_max_playlist_size]
        await message.answer(
            f"⚠️ Больше {_max_playlist_size} треков. Обработаю первые {len(tracks)}.",
        )

    if is_user_busy(user_id):
        await message.answer(
            "⏳ <b>Задача уже выполняется</b>\n\n"
            "Дождитесь завершения текущей обработки.\n"
            "Проверить статус: /status",
            parse_mode=ParseMode.HTML,
        )
        return

    # Определяем канал: из плейлиста → из hint → из storage
    channel_id = parsed.channel_id
    if channel_id is None and channel_hint:
        channel_id = _resolve_channel_id(channel_hint, user_id)
    if channel_id is None:
        channel_id = _storage.get_default_channel(user_id)

    if channel_id is None:
        await message.answer(
            "❌ <b>Канал не определён</b>\n\n"
            "Привяжите канал:\n"
            "<code>/link -100XXXXXXXXXX</code>\n\n"
            "Или укажите chat_id перед треками:\n"
            "<code>-100XXXXXXXXXX\nИсполнитель - Название</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not _storage.has_channel(user_id, channel_id):
        _storage.add_channel(user_id, channel_id)

    task_id = await process_playlist(
        user_id=user_id,
        channel_id=channel_id,
        tracks=tracks,
        playlist_name=parsed.name,
        total_duration=parsed.total_duration,
        bot=message.bot,
        cache_mgr=_cache_mgr,
        scraper=_scraper,
        downloader=_downloader,
        fuzzy_threshold=_fuzzy_threshold,
        rate_limit=_rate_limit,
    )

    # Формируем краткое описание
    duration_str = _format_duration(parsed.total_duration) if parsed.total_duration else "?"
    playlist_desc = f"{parsed.name} ({len(tracks)} треков, ~{duration_str})" if parsed.name else f"{len(tracks)} треков, ~{duration_str}"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Проверить статус", callback_data=f"refresh_{task_id}")],
    ])

    await message.answer(
        f"🚀 <b>Задача запущена!</b>\n\n"
        f"📋 ID: <code>{task_id}</code>\n"
        f"🎵 Плейлист: {playlist_desc}\n"
        f"📡 Канал: <code>{channel_id}</code>\n\n"
        f"⏱ Ожидаемое время: {len(tracks) * 3 // 60} мин\n\n"
        f"📊 Статус: /status {task_id}",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )

    log.info(
        "playlist_task_created",
        task_id=task_id,
        user_id=user_id,
        channel_id=channel_id,
        tracks=len(tracks),
    )


@router.message(F.chat.type == "private", F.document)
async def handle_playlist_file(message: Message):
    """Принимает .txt файл со списком треков."""
    document = message.document
    if not document or not document.file_name:
        await message.answer("❌ Не удалось определить файл.")
        return

    if not document.file_name.lower().endswith(".txt"):
        await message.answer("❌ Пожалуйста, отправьте файл в формате <b>.txt</b>.", parse_mode=ParseMode.HTML)
        return

    if not all([_scraper, _downloader, _cache_mgr, _storage]):
        await message.answer("⚠️ Бот не полностью инициализирован. Попробуйте позже.")
        return

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            tmp_path = tmp.name
        await message.bot.download(document.file_id, destination=tmp_path)

        with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        log.error("file_download_error", error=str(e))
        await message.answer("❌ Ошибка при скачивании файла.")
        return
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # Первую строку файла можно использовать как hint для channel_id, если это число
    first_line = content.splitlines()[0] if content else ""
    await _start_processing(message, content, channel_hint=first_line)


@router.message(F.chat.type == "private", F.text & ~F.text.startswith("/"))
async def handle_playlist_text(message: Message):
    """Обработка текстового плейлиста из сообщения (не команда)."""
    text = message.text or ""
    if not all([_scraper, _downloader, _cache_mgr, _storage]):
        await message.answer("⚠️ Бот не полностью инициализирован. Попробуйте позже.")
        return

    # Если первое слово — chat_id, отделяем его
    lines = text.splitlines()
    channel_hint = lines[0] if lines else None
    # Если первая строка выглядит как chat_id — убираем её из треков
    if channel_hint and channel_hint.strip().lstrip("-").isdigit():
        raw_text = "\n".join(lines[1:])
    else:
        raw_text = text
        channel_hint = None

    await _start_processing(message, raw_text, channel_hint=channel_hint)
