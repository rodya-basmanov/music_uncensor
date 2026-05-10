"""Процессор плейлистов: полный цикл обработки треков."""

import asyncio
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from aiogram import Bot

from core.cache_manager import CacheManager
from core.downloader import Downloader
from core.matcher import match_track
from tinytag import TinyTag
from core.scraper.mp3party import Mp3PartyScraper
from core.scraper.soundcloud import search_and_download as sc_search_download
from services.tg_sender import TelegramSender
from utils.helpers import Track, generate_hash, clean_artist_for_search, clean_title_for_search
from utils.logger import get_logger

log = get_logger("processor")


@dataclass
class TaskStatus:
    """Статус задачи обработки плейлиста."""
    task_id: str
    user_id: int
    channel_id: int
    total: int = 0
    processed: int = 0
    sent: int = 0
    cached: int = 0
    not_found: int = 0
    errors: int = 0
    failed_tracks: List[str] = field(default_factory=list)
    done: bool = False
    started_at: float = 0.0


# Глобальное хранилище задач (в MVP — в памяти)
_tasks: Dict[str, TaskStatus] = {}
# Активные задачи по user_id (для ограничения 1 задача/юзер)
_active_users: set = set()


def get_task_status(task_id: str) -> Optional[TaskStatus]:
    """Получает статус задачи по ID."""
    return _tasks.get(task_id)


def is_user_busy(user_id: int) -> bool:
    """Проверяет, есть ли у пользователя активная задача."""
    return user_id in _active_users


async def process_playlist(
    user_id: int,
    channel_id: int,
    tracks: List[Track],
    bot: Bot,
    cache_mgr: CacheManager,
    scraper: Mp3PartyScraper,
    downloader: Downloader,
    fuzzy_threshold: int = 85,
    rate_limit: float = 2.0,
) -> str:
    """
    Запускает обработку плейлиста как фоновую задачу.
    
    Returns:
        task_id
    """
    task_id = str(uuid.uuid4())[:8]
    status = TaskStatus(
        task_id=task_id,
        user_id=user_id,
        channel_id=channel_id,
        total=len(tracks),
        started_at=time.time(),
    )
    _tasks[task_id] = status
    _active_users.add(user_id)

    # Запускаем обработку в фоне
    asyncio.create_task(
        _process_tracks(status, tracks, bot, cache_mgr, scraper, downloader, fuzzy_threshold, rate_limit)
    )

    return task_id


async def _process_tracks(
    status: TaskStatus,
    tracks: List[Track],
    bot: Bot,
    cache_mgr: CacheManager,
    scraper: Mp3PartyScraper,
    downloader: Downloader,
    fuzzy_threshold: int,
    rate_limit: float,
) -> None:
    """Внутренний цикл обработки треков."""
    sender = TelegramSender(bot)

    try:
        for i, track in enumerate(tracks):
            status.processed = i + 1
            result = await _process_single_track(
                track, status.channel_id, cache_mgr, scraper,
                downloader, sender, fuzzy_threshold,
            )

            if result == "sent_cached_file_id":
                status.sent += 1
                status.cached += 1
            elif result == "sent_cached_file":
                status.sent += 1
                status.cached += 1
            elif result == "sent_new":
                status.sent += 1
            elif result == "not_found":
                status.not_found += 1
                status.failed_tracks.append(f"{track.artist} - {track.title}")
            else:  # error
                status.errors += 1
                status.failed_tracks.append(f"{track.artist} - {track.title} (ошибка)")

            # Отправляем прогресс каждые 5 треков
            if status.processed % 5 == 0 and status.processed < status.total:
                try:
                    await bot.send_message(
                        status.user_id,
                        f"⏳ Прогресс: {status.processed}/{status.total}\n"
                        f"✅ Отправлено: {status.sent} | ⚡ Из кэша: {status.cached} | "
                        f"❌ Не найдено: {status.not_found}",
                    )
                except Exception:
                    pass

            # Задержка между треками (анти-бан)
            if i < len(tracks) - 1:
                await asyncio.sleep(random.uniform(rate_limit, rate_limit + 2))

        # Финальный отчёт
        status.done = True
        elapsed = int(time.time() - status.started_at)
        report = (
            f"🏁 Плейлист обработан!\n\n"
            f"📊 Результат ({task_id_display(status.task_id)}):\n"
            f"  ✅ Отправлено: {status.sent}/{status.total}\n"
            f"  ⚡ Из кэша: {status.cached}\n"
            f"  ❌ Не найдено: {status.not_found}\n"
            f"  ⚠️ Ошибок: {status.errors}\n"
            f"  ⏱ Время: {elapsed} сек"
        )
        if status.failed_tracks:
            report += "\n\n❌ Не найдены:\n"
            for ft in status.failed_tracks[:20]:
                report += f"  • {ft}\n"

        try:
            await bot.send_message(status.user_id, report)
        except Exception as e:
            log.error("report_send_error", error=str(e))

    except Exception as e:
        log.error("playlist_processing_error", error=str(e), task_id=status.task_id)
        status.done = True
        try:
            await bot.send_message(
                status.user_id,
                f"❌ Ошибка при обработке плейлиста: {str(e)[:200]}",
            )
        except Exception:
            pass
    finally:
        _active_users.discard(status.user_id)


async def _process_single_track(
    track: Track,
    channel_id: int,
    cache_mgr: CacheManager,
    scraper: Mp3PartyScraper,
    downloader: Downloader,
    sender: TelegramSender,
    fuzzy_threshold: int,
) -> str:
    """
    Обрабатывает один трек: кэш → поиск → download → send.
    
    Returns:
        Статус: sent_cached_file_id, sent_cached_file, sent_new, not_found, error
    """
    hash_key = generate_hash(track.artist, track.title)

    try:
        # 1. Проверяем кэш
        cached = cache_mgr.get(hash_key)
        if cached:
            file_id = cached.get("telegram_file_id")
            file_path = cached.get("file_path", "")

            if file_id:
                try:
                    await sender.send_by_file_id(channel_id, file_id, track)
                    cache_mgr.update_access(hash_key)
                    return "sent_cached_file_id"
                except Exception:
                    # file_id может устареть — пробуем файл
                    pass

            if file_path and os.path.exists(file_path):
                new_file_id = await sender.send_file(channel_id, file_path, track)
                if new_file_id:
                    cache_mgr.save_file_id(hash_key, new_file_id)
                return "sent_cached_file"

        # 2. Подготавливаем поисковые запросы (чистые — без feat./prod.)
        search_artist = clean_artist_for_search(track.artist)
        search_title = clean_title_for_search(track.title)

        # 3. Пробуем SoundCloud (основной источник)
        sc_dir = os.path.join("./data/cache", "sc")
        os.makedirs(sc_dir, exist_ok=True)
        sc_path = await asyncio.to_thread(
            sc_search_download, search_artist, search_title, sc_dir, track.duration
        )
        if sc_path:
            # Переименовываем sc_tmp.mp3 в {hash}.mp3 чтобы не перезаписывать
            final_sc_path = os.path.join(sc_dir, f"{hash_key}.mp3")
            if sc_path != final_sc_path and os.path.exists(sc_path):
                if os.path.exists(final_sc_path):
                    os.remove(final_sc_path)
                os.rename(sc_path, final_sc_path)
                sc_path = final_sc_path
            file_id = await sender.send_file(channel_id, sc_path, track)
            cache_mgr.save(hash_key, {
                "artist": track.artist,
                "title": track.title,
                "file_path": sc_path,
                "telegram_file_id": file_id or "",
                "source": "soundcloud",
                "file_size": os.path.getsize(sc_path),
                "downloaded_at": int(time.time()),
            })
            return "sent_new"

        # 4. SoundCloud не нашёл — fallback на mp3party
        log.info("soundcloud_not_found_mp3party_fallback", artist=track.artist, title=track.title)
        results = await scraper.search_async(search_artist, search_title)
        best = None
        candidates = []

        if results:
            candidates = [
                {"artist": r.artist, "title": r.title, "mp3_url": r.mp3_url, "page_url": r.page_url}
                for r in results
            ]
            best = match_track(search_artist, search_title, candidates, fuzzy_threshold)

        if not best:
            log.info("track_not_found_anywhere", artist=track.artist, title=track.title)
            return "not_found"

        # 4. Задержка между поиском и скачиванием
        await asyncio.sleep(random.uniform(2.0, 4.0))

        # 5. Скачиваем: пробуем best match, затем fallback на других кандидатах
        file_path = None
        all_candidates = [best] + [c for c in candidates if c != best]
        for candidate in all_candidates[:3]:  # Max 3 попытки
            file_path = await downloader.download_async(
                candidate["mp3_url"], hash_key, candidate.get("page_url", "")
            )
            if file_path:
                best = candidate
                break
            log.info("download_candidate_failed", url=candidate["mp3_url"], trying_next=True)
            await asyncio.sleep(random.uniform(1.0, 2.0))

        if not file_path:
            log.warning("download_failed_all_candidates", artist=track.artist, title=track.title)
            return "error"

        # 5.5 Проверяем длительность если есть в VK
        if track.duration > 0:
            try:
                tag = TinyTag.get(file_path)
                if tag.duration:
                    diff = abs(tag.duration - track.duration)
                    if diff > 5:
                        log.warning(
                            "duration_mismatch",
                            artist=track.artist,
                            title=track.title,
                            expected=track.duration,
                            actual=round(tag.duration),
                            diff=round(diff),
                        )
                        # Удаляем файл и пробуем следующего кандидата
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        # Пробуем остальных кандидатов
                        remaining = [c for c in all_candidates if c != best and c not in all_candidates[:all_candidates.index(best)+1]]
                        for candidate in remaining[:2]:
                            file_path = await downloader.download_async(
                                candidate["mp3_url"], hash_key, candidate.get("page_url", "")
                            )
                            if file_path:
                                try:
                                    tag2 = TinyTag.get(file_path)
                                    if tag2.duration and abs(tag2.duration - track.duration) <= 5:
                                        best = candidate
                                        break
                                except Exception:
                                    break
                            log.info("duration_retry_failed", url=candidate["mp3_url"])
                            await asyncio.sleep(random.uniform(1.0, 2.0))
                        else:
                            file_path = None
                        if not file_path:
                            log.warning("duration_all_candidates_mismatch", artist=track.artist, title=track.title)
                            # Пробуем SoundCloud как fallback
                            sc_path = await asyncio.to_thread(
                                sc_search_download, search_artist, search_title, sc_dir, track.duration
                            )
                            if sc_path:
                                final_sc_path = os.path.join(sc_dir, f"{hash_key}.mp3")
                                if sc_path != final_sc_path and os.path.exists(sc_path):
                                    if os.path.exists(final_sc_path):
                                        os.remove(final_sc_path)
                                    os.rename(sc_path, final_sc_path)
                                    sc_path = final_sc_path
                                file_id = await sender.send_file(channel_id, sc_path, track)
                                cache_mgr.save(hash_key, {
                                    "artist": track.artist,
                                    "title": track.title,
                                    "file_path": sc_path,
                                    "telegram_file_id": file_id or "",
                                    "source": "soundcloud",
                                    "file_size": os.path.getsize(sc_path),
                                    "downloaded_at": int(time.time()),
                                })
                                return "sent_new"
                            return "error"
            except Exception as e:
                log.warning("duration_check_error", error=str(e))

        # 6. Отправляем
        file_id = await sender.send_file(channel_id, file_path, track)

        # 7. Сохраняем в кэш
        cache_mgr.save(hash_key, {
            "artist": track.artist,
            "title": track.title,
            "file_path": file_path,
            "telegram_file_id": file_id or "",
            "source_url": best.get("page_url", ""),
            "file_size": os.path.getsize(file_path),
            "downloaded_at": int(time.time()),
        })

        return "sent_new"

    except Exception as e:
        log.error(
            "track_processing_error",
            artist=track.artist,
            title=track.title,
            error=str(e),
        )
        return "error"


def task_id_display(task_id: str) -> str:
    """Форматирует task_id для отображения."""
    return f"<code>{task_id}</code>"
