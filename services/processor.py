"""Процессор плейлистов: полный цикл обработки треков."""

import asyncio
import html
import os
import random
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from rapidfuzz import fuzz

from core.cache_manager import CacheManager
from core.cover import download_image
from core.scraper import soundcloud, youtube
from core.scraper.common import (
    Candidate,
    DownloadResult,
    download_audio,
    normalize,
    rank_candidates,
    score_candidate,
    split_artist_title,
)
from services.tg_sender import TelegramSender, bot_username
from utils.escape import esc_html, esc_md
from utils.ffmpeg import make_thumbnail
from utils.helpers import (
    Track,
    clean_artist_for_search,
    clean_title_for_display,
    clean_title_for_search,
    format_duration,
    generate_hash,
)
from utils.links import source_label
from utils.logger import get_logger
from utils.rich import edit_rich, send_rich
from utils.ui import is_mashup, progress_bar

log = get_logger("processor")

PROVIDERS = {
    "youtube": youtube.search,
    "soundcloud": soundcloud.search,
}

# Минимальный скор кандидата для структурных треков (с артистом)
MIN_MATCH_SCORE = 55

# VK API клиент (регистрируется, если задан VK_TOKEN)
_vk_fetcher = None
# Статус VK-токена: ok / expired / error / missing / unknown
_vk_status: str = "unknown"


def set_vk_token(token: str) -> None:
    """Регистрирует VK-клиент для поиска/загрузки (вызывается при инициализации)."""
    global _vk_fetcher, _vk_status
    if token:
        from core.vk_fetcher import VKFetcher
        _vk_fetcher = VKFetcher(token)
        _vk_status = "unknown"
    else:
        _vk_fetcher = None
        _vk_status = "missing"


def vk_token_status() -> str:
    """Статус VK-токена: ok / expired / error / missing / unknown."""
    return _vk_status


def _vk_search(query: str, count: int = 5, proxy: str = "") -> list:
    global _vk_status
    if not _vk_fetcher:
        return []
    result = _vk_fetcher.search(query, count)
    if _vk_fetcher.auth_failed:
        _vk_status = "expired"
    elif _vk_fetcher.last_error:
        _vk_status = "error"
    return result


async def warm_vk() -> None:
    """Прогрев VK-сессии на старте + проверка токена."""
    global _vk_status
    if not _vk_fetcher:
        _vk_status = "missing"
        return
    _vk_fetcher.auth_failed = False
    _vk_fetcher.last_error = ""
    try:
        await asyncio.to_thread(_vk_fetcher.search, "music", 1)
    except Exception as e:
        _vk_status = "error"
        log.warning("vk_warmup_failed", error=str(e))
        return

    if _vk_fetcher.auth_failed:
        _vk_status = "expired"
        log.warning("vk_token_expired")
        from services import health
        await health.alert_owner(
            "VK_TOKEN истёк или недействителен — обновите токен в <code>.env</code>.",
            key="vk_token",
        )
    elif _vk_fetcher.last_error:
        _vk_status = "error"
        log.warning("vk_warmup_failed", error=_vk_fetcher.last_error)
    else:
        _vk_status = "ok"
        log.info("vk_warmed_up")


# Кэш результатов поиска: (source, query) -> (timestamp, [Candidate])
_SEARCH_CACHE_TTL = 600
_SEARCH_CACHE_MAX = 300
_search_cache: Dict[tuple, tuple] = {}


def _cache_key(source: str, query: str) -> tuple:
    return (source, " ".join((query or "").lower().split()))


def _cache_get(source: str, query: str):
    item = _search_cache.get(_cache_key(source, query))
    if item and time.time() - item[0] < _SEARCH_CACHE_TTL:
        return item[1]
    return None


def _cache_put(source: str, query: str, candidates: list) -> None:
    if len(_search_cache) >= _SEARCH_CACHE_MAX:
        for k, _ in sorted(_search_cache.items(), key=lambda kv: kv[1][0])[:50]:
            _search_cache.pop(k, None)
    _search_cache[_cache_key(source, query)] = (time.time(), candidates)


def _provider(name: str):
    """Возвращает функцию поиска по имени источника."""
    if name == "vk":
        return _vk_search
    return PROVIDERS.get(name)


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
    failed_objects: List[Track] = field(default_factory=list)
    done: bool = False
    cancelled: bool = False
    started_at: float = 0.0
    mode: str = "uncensored"
    status_message_id: int = 0
    status_chat_id: int = 0
    current_track: str = ""
    last_edit: float = 0.0
    playlist_name: str | None = None
    total_duration: int = 0


# Глобальное хранилище задач (в MVP — в памяти)
_tasks: Dict[str, TaskStatus] = {}
# Активные задачи по user_id (для ограничения 1 задача/юзер)
_active_users: set = set()

# Кнопка «Не тот трек» под аудио в канале: token -> {query, channel_id, user_id, created}
_redo_pending: Dict[str, dict] = {}


def register_redo(query: str, channel_id: int, user_id: int) -> str:
    """Регистрирует запрос для кнопки «Не тот трек» и возвращает токен."""
    now = time.time()
    for t in [t for t, v in _redo_pending.items() if now - v["created"] > 3600]:
        _redo_pending.pop(t, None)
    token = uuid.uuid4().hex[:8]
    _redo_pending[token] = {
        "query": query, "channel_id": channel_id, "user_id": user_id, "created": now,
    }
    return token


def get_redo(token: str) -> Optional[dict]:
    return _redo_pending.get(token)


# «Докачать не найденные»: token -> {tracks, channel_id, user_id, mode, created}
_retry_pending: Dict[str, dict] = {}


def register_retry(tracks: List[Track], channel_id: int, user_id: int, mode: str) -> str:
    now = time.time()
    for t in [t for t, v in _retry_pending.items() if now - v["created"] > 3600]:
        _retry_pending.pop(t, None)
    token = uuid.uuid4().hex[:8]
    _retry_pending[token] = {
        "tracks": tracks, "channel_id": channel_id, "user_id": user_id,
        "mode": mode, "created": now,
    }
    return token


def get_retry(token: str) -> Optional[dict]:
    return _retry_pending.get(token)


def _redo_keyboard(query: str, channel_id: int, user_id: int) -> Optional[InlineKeyboardMarkup]:
    """Кнопка «Не тот трек» (если известен пользователь)."""
    if not user_id or not query:
        return None
    token = register_redo(query, channel_id, user_id)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔁 Не тот трек", callback_data=f"fix|{token}", style="primary"),
    ]])

# Статистика (глобальная)
_stats = {
    "total_playlists": 0,
    "total_tracks": 0,
    "total_sent": 0,
    "total_cached": 0,
    "total_not_found": 0,
    "total_time": 0,
}

_stats_path: str = ""
_last_stats_save: float = 0.0

# Подпись под аудио (song.link + via @bot)
_caption_enabled: bool = True


def set_caption(enabled: bool) -> None:
    global _caption_enabled
    _caption_enabled = bool(enabled)


def _build_caption(source_url: str) -> Optional[str]:
    """Подпись под аудио: источник + кликабельное «via @бот».

    Для VK-ссылки на mp3 сам файл не линкуем (некрасиво) — только метку «VK».
    """
    if not _caption_enabled or not source_url:
        return None
    label = source_label(source_url)
    if not label:
        return None
    if label == "VK":
        parts = ["🎧 VK"]
    else:
        src = html.escape(source_url, quote=True)
        parts = [f'🎧 <a href="{src}">{label}</a>']
    user = bot_username()
    if user:
        parts.append(f'<a href="https://t.me/{user}">via</a>')
    return " · ".join(parts)


# Минимальный fuzzy-скор для использования VK-обложки (иначе — обложка источника)
_vk_cover_min_score: float = 90.0


def set_vk_cover_min_score(score: float) -> None:
    global _vk_cover_min_score
    try:
        _vk_cover_min_score = float(score)
    except (TypeError, ValueError):
        _vk_cover_min_score = 90.0


def _fetch_vk_cover(artist: str, title: str) -> str:
    """Ищет обложку в VK; возвращает URL только при хорошем совпадении (иначе пусто)."""
    if not _vk_fetcher or not (artist or title):
        return ""
    query = f"{artist} {title}".strip()
    try:
        cands = _vk_fetcher.search(query, 3)
    except Exception:
        return ""
    best_url = ""
    best_score = 0.0
    for c in cands:
        if not c.cover_url:
            continue
        title_score = fuzz.token_set_ratio(normalize(title), normalize(c.title))
        artist_score = fuzz.token_set_ratio(normalize(artist), normalize(c.artist or c.uploader))
        score = 0.6 * title_score + 0.4 * artist_score
        if score > best_score:
            best_score, best_url = score, c.cover_url
    if best_url and best_score >= _vk_cover_min_score:
        log.info("vk_cover_found", score=round(best_score, 1))
        return best_url
    return ""


def set_stats_path(path: str) -> None:
    """Задаёт файл для хранения статистики и загружает её."""
    global _stats_path
    _stats_path = path
    if not path or not os.path.exists(path):
        return
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in _stats:
            if isinstance(data.get(k), (int, float)):
                _stats[k] = data[k]
    except Exception as e:
        log.warning("stats_load_error", error=str(e))


def _save_stats(force: bool = False) -> None:
    """Сохраняет статистику на диск (не чаще раза в 2 секунды)."""
    global _last_stats_save
    if not _stats_path:
        return
    now = time.time()
    if not force and now - _last_stats_save < 2:
        return
    _last_stats_save = now
    try:
        import json
        os.makedirs(os.path.dirname(_stats_path), exist_ok=True)
        tmp = _stats_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_stats, f, ensure_ascii=False)
        os.replace(tmp, _stats_path)
    except Exception as e:
        log.warning("stats_save_error", error=str(e))


def _bump(**deltas) -> None:
    """Увеличивает глобальные счётчики и сохраняет статистику."""
    for k, v in deltas.items():
        if k in _stats:
            _stats[k] += v
    _save_stats()


def get_stats() -> dict:
    """Возвращает статистику использования бота."""
    return _stats.copy()


def get_task_status(task_id: str) -> Optional[TaskStatus]:
    """Получает статус задачи по ID."""
    return _tasks.get(task_id)


def cleanup_old_tasks(max_age_minutes: int = 30) -> int:
    """Очищает старые завершённые задачи из памяти."""
    global _tasks
    now = time.time()
    cutoff = now - (max_age_minutes * 60)
    to_delete = [tid for tid, status in _tasks.items()
                 if status.done and status.started_at < cutoff]
    for tid in to_delete:
        del _tasks[tid]
    return len(to_delete)


def is_user_busy(user_id: int) -> bool:
    """Проверяет, есть ли у пользователя активная задача."""
    return user_id in _active_users


def active_task_count() -> int:
    """Сколько задач сейчас выполняется."""
    return sum(1 for s in _tasks.values() if not s.done)


def cancel_user_task(user_id: int) -> str | None:
    """Отменяет активную задачу пользователя. Returns task_id или None."""
    if user_id not in _active_users:
        return None

    for task_id, status in _tasks.items():
        if status.user_id == user_id and not status.done:
            status.done = True
            status.cancelled = True
            _active_users.discard(user_id)
            log.info("task_cancelled", task_id=task_id, user_id=user_id)
            return task_id
    return None


def cancel_task(task_id: str) -> bool:
    """Отменяет задачу по её ID. Returns True если отменили."""
    status = _tasks.get(task_id)
    if not status or status.done:
        return False
    status.done = True
    status.cancelled = True
    _active_users.discard(status.user_id)
    log.info("task_cancelled_by_id", task_id=task_id)
    return True


async def process_playlist(
    user_id: int,
    channel_id: int,
    tracks: List[Track],
    bot: Bot,
    cache_mgr: CacheManager,
    playlist_name: str | None = None,
    total_duration: int = 0,
    rate_limit: float = 2.0,
    source_order: List[str] | None = None,
    ytdlp_proxy: str = "",
    mode: str = "uncensored",
    concurrency: int = 3,
) -> str:
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

    task = asyncio.create_task(
        _process_tracks(
            status, tracks, bot, cache_mgr, rate_limit,
            playlist_name, total_duration, source_order or ["youtube", "soundcloud"], ytdlp_proxy, mode,
            concurrency,
        )
    )
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    return task_id


def _status_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{task_id}", style="primary"),
        InlineKeyboardButton(text="⏹ Отменить", callback_data=f"cancel_task|{task_id}", style="danger"),
    ]])


def _eta_str(status: TaskStatus, final: bool) -> str:
    """Оценка оставшегося времени (ETA) по средней скорости обработки."""
    if final or status.processed <= 0 or status.processed >= status.total:
        return ""
    elapsed = time.time() - status.started_at
    avg = elapsed / status.processed
    secs = int(avg * (status.total - status.processed))
    if secs <= 0:
        return ""
    return format_duration(secs)


def _render_status(status: TaskStatus, final: bool = False) -> str:
    """Единый текст живой статус-записи (HTML)."""
    if final:
        head = "⏹️ <b>Задача отменена</b>" if status.cancelled else "🏁 <b>Плейлист обработан</b>"
    else:
        head = "⏳ <b>Обработка плейлиста</b>"

    lines = [head]
    if status.playlist_name:
        lines.append(f"🎧 {esc_html(status.playlist_name)}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(progress_bar(status.processed, status.total))
    lines.append(f"📊 <b>{status.processed}</b>/{status.total}")
    if not final and status.current_track:
        lines.append(f"🎵 {esc_html(status.current_track[:50])}")
    lines.append(f"✅ {status.sent}   ⚡ {status.cached}   ❌ {status.not_found}   ⚠️ {status.errors}")
    eta = _eta_str(status, final)
    if eta:
        lines.append(f"⏳ Осталось: ~{eta}")
    if final:
        lines.append(f"⏱ Время: {int(time.time() - status.started_at)} сек")
    lines.append("")
    lines.append(f"ID: <code>{status.task_id}</code>" + ("" if final else "   ·   /cancel"))
    if final and status.failed_tracks:
        lines.append("\n❌ <b>Не найдены:</b>")
        for ft in status.failed_tracks[:20]:
            lines.append(f"  • {esc_html(ft)}")
    return "\n".join(lines)


async def _edit_status(bot: Bot, status: TaskStatus, final: bool = False, force: bool = False) -> None:
    """Редактирует живое сообщение статуса (rich, с троттлингом)."""
    if not status.status_message_id or not status.status_chat_id:
        return
    now = time.time()
    if not force and now - status.last_edit < 2.0:
        return
    status.last_edit = now
    if final:
        markup = None
        if status.failed_objects and not status.cancelled:
            token = register_retry(
                status.failed_objects, status.channel_id, status.user_id, status.mode,
            )
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text=f"🔁 Докачать не найденные ({len(status.failed_objects)})",
                    callback_data=f"retry|{token}", style="primary",
                ),
            ]])
    else:
        markup = _status_keyboard(status.task_id)
    await edit_rich(
        bot,
        status.status_chat_id,
        status.status_message_id,
        _render_status_md(status, final=final),
        reply_markup=markup,
        fallback=_render_status(status, final=final),
    )


def _render_status_md(status: TaskStatus, final: bool = False) -> str:
    """Живой статус в rich-markdown."""
    if final:
        head = "# ⏹️ Задача отменена" if status.cancelled else "# 🏁 Плейлист обработан"
    else:
        head = "# ⏳ Обработка плейлиста"

    lines = [head]
    if status.playlist_name:
        lines.append(f"🎧 **{esc_md(status.playlist_name)}**")
    lines.append("")
    lines.append(progress_bar(status.processed, status.total))
    lines.append(f"**{status.processed}**/{status.total}")
    if not final and status.current_track:
        lines.append(f"🎵 {esc_md(status.current_track[:50])}")
    lines.append(f"✅ {status.sent}   ⚡ {status.cached}   ❌ {status.not_found}   ⚠️ {status.errors}")
    eta = _eta_str(status, final)
    if eta:
        lines.append(f"⏳ Осталось: ~{eta}")
    if final:
        lines.append(f"⏱ Время: {int(time.time() - status.started_at)} сек")
    lines.append("")
    lines.append(f"ID: `{status.task_id}`" + ("" if final else "   ·   /cancel"))
    if final and status.failed_tracks:
        lines.append("")
        lines.append("**❌ Не найдены:**")
        for ft in status.failed_tracks[:20]:
            lines.append(f"- {esc_md(ft)}")
    return "\n".join(lines)


def _track_label(track: Track) -> str:
    if track.artist or track.title:
        return f"{track.artist} - {track.title}".strip(" -")
    return track.query or ""


def _apply_result(status: TaskStatus, track: Track, result: str) -> None:
    """Обновляет счётчики/список по результату обработки трека."""
    if result in ("sent_cached_file_id", "sent_cached_file"):
        status.sent += 1
        status.cached += 1
    elif result == "sent_new":
        status.sent += 1
    elif result == "not_found":
        status.not_found += 1
        status.failed_tracks.append(_track_label(track))
        status.failed_objects.append(track)
    else:
        status.errors += 1
        status.failed_tracks.append(f"{_track_label(track)} (ошибка)")
        status.failed_objects.append(track)


async def _process_tracks(
    status: TaskStatus,
    tracks: List[Track],
    bot: Bot,
    cache_mgr: CacheManager,
    rate_limit: float,
    playlist_name: str | None = None,
    total_duration: int = 0,
    source_order: List[str] | None = None,
    ytdlp_proxy: str = "",
    mode: str = "uncensored",
    concurrency: int = 1,
) -> None:
    """Внутренний цикл обработки треков (последовательно или пулом воркеров)."""
    sender = TelegramSender(bot)
    source_order = source_order or ["youtube", "soundcloud"]
    status.playlist_name = playlist_name
    status.total_duration = total_duration
    status.mode = mode

    try:
        # Живое статус-сообщение (само обновляется)
        try:
            status.status_chat_id = status.user_id
            msg = await send_rich(
                bot, status.user_id, _render_status_md(status),
                reply_markup=_status_keyboard(status.task_id),
                fallback=_render_status(status),
            )
            if msg:
                status.status_message_id = msg.message_id
                status.last_edit = time.time()
        except Exception as e:
            log.error("status_message_send_error", error=str(e))

        if playlist_name or total_duration:
            duration_str = format_duration(total_duration) if total_duration else "?"
            try:
                await send_rich(
                    bot, status.channel_id,
                    f"# 🎧 {esc_md(playlist_name or 'Плейлист')}\n\n🎵 Треков: **{len(tracks)}**   ⏱ ~{duration_str}",
                    fallback=(
                        f"🎧 <b>{esc_html(playlist_name or 'Плейлист')}</b>\n"
                        f"🎵 Треков: <b>{len(tracks)}</b>   ⏱ ~{duration_str}"
                    ),
                )
            except Exception as e:
                log.warning("playlist_header_send_error", error=str(e))

        if concurrency <= 1 or len(tracks) <= 1:
            for i, track in enumerate(tracks):
                if status.cancelled:
                    log.info("task_stopped_by_user", task_id=status.task_id, processed=status.processed)
                    break
                status.processed = i + 1
                status.current_track = _track_label(track)
                await _edit_status(bot, status)

                result = await _process_single_track(
                    track, status.channel_id, cache_mgr, sender, source_order, ytdlp_proxy, mode, status.user_id,
                )
                _apply_result(status, track, result)
                await _edit_status(bot, status)

                if i < len(tracks) - 1 and mode != "vk":
                    await asyncio.sleep(random.uniform(rate_limit, rate_limit + 2))
        else:
            lock = asyncio.Lock()
            queue: asyncio.Queue = asyncio.Queue()
            for t in tracks:
                queue.put_nowait(t)

            async def worker() -> None:
                while not status.cancelled:
                    try:
                        track = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        result = await _process_single_track(
                            track, status.channel_id, cache_mgr, sender, source_order, ytdlp_proxy, mode, status.user_id,
                        )
                    except Exception as e:
                        log.error("track_worker_error", error=str(e), title=track.title)
                        result = "error"

                    async with lock:
                        status.processed += 1
                        status.current_track = _track_label(track)
                        _apply_result(status, track, result)
                        await _edit_status(bot, status)

                    if mode != "vk":
                        await asyncio.sleep(random.uniform(rate_limit, rate_limit + 2))

            n = max(1, min(concurrency, len(tracks)))
            workers = [asyncio.create_task(worker()) for _ in range(n)]
            await asyncio.gather(*workers, return_exceptions=True)

        status.done = True
        status.current_track = ""
        elapsed = int(time.time() - status.started_at)

        if not status.cancelled:
            _bump(total_playlists=1, total_time=elapsed)
        _save_stats(force=True)

        # Финализируем то же сообщение
        if status.status_message_id:
            await _edit_status(bot, status, final=True, force=True)
        else:
            try:
                await send_rich(
                    bot, status.user_id, _render_status_md(status, final=True),
                    fallback=_render_status(status, final=True),
                )
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
        if len(_tasks) > 100:
            cleanup_old_tasks()


def _is_junk(s: str) -> bool:
    s = (s or "").strip()
    return (not s) or s.isdigit() or len(s) <= 1


_SEP = re.compile(r"\s+[-–—]\s+")


def _resolve_display(dr: DownloadResult, cand: Candidate, artist_hint: str) -> tuple[str, str]:
    """Возвращает (artist, title) для метаданных Telegram.

    Приоритет: структура заголовка "Artist - Title" > поля источника.
    Отсекает мусорные uploader'ы вроде "5".
    """
    raw_title = (dr.title or cand.title or "").strip()
    raw_artist = (dr.artist or cand.artist or "").strip()

    if _SEP.search(raw_title):
        a, t = split_artist_title(raw_title, "")
        artist = a if not _is_junk(a) else (raw_artist or artist_hint or cand.uploader or "Unknown Artist")
        title = t
    else:
        title = clean_title_for_display(raw_title)
        if not _is_junk(raw_artist):
            artist = raw_artist
        elif not _is_junk(cand.uploader):
            artist = cand.uploader
        else:
            artist = artist_hint or "Unknown Artist"

    if not title.strip():
        title = raw_title or cand.title or "Unknown"

    return artist.strip(), title.strip()


async def _download_vk_media(
    media_url: str,
    cover_url: str,
    work_dir: str,
    cache_mgr: CacheManager,
    hash_key: str,
    ytdlp_proxy: str,
    artist: str = "",
    title: str = "",
) -> tuple[Optional[str], str]:
    """Скачивает аудио из VK (mp3/HLS) + обложку. Возвращает (mp3_path, cover_path)."""
    dr = await asyncio.to_thread(download_audio, media_url, work_dir, "audio", "", "vk", False)
    if not dr or not dr.file_path:
        return None, ""

    final_mp3 = cache_mgr._file_path(hash_key)
    if os.path.exists(final_mp3):
        try:
            os.remove(final_mp3)
        except OSError:
            pass
    os.replace(dr.file_path, final_mp3)

    cover_path = ""
    final_cover = cache_mgr._cover_path(hash_key)
    if cover_url:
        raw = os.path.join(work_dir, "cover")
        if await download_image(cover_url, raw, ""):
            if make_thumbnail(raw, final_cover):
                cover_path = final_cover
    return final_mp3, cover_path


async def _download_and_send(
    cand: Candidate,
    channel_id: int,
    cache_mgr: CacheManager,
    sender: TelegramSender,
    ytdlp_proxy: str,
    hash_key: str,
    artist_hint: str = "",
    duration_hint: int = 0,
    original_artist: str = "",
    original_title: str = "",
    query: str = "",
    vk_cover_url: str = "",
    user_id: int = 0,
    display_override: Optional[tuple[str, str]] = None,
) -> Optional[dict]:
    """Скачивает конкретного кандидата и отправляет в канал.

    Для source="vk" — прямая загрузка из VK; иначе yt-dlp (YouTube/SoundCloud).
    display_override — форсировать (artist, title) в метаданных (напр. из альбома).
    Returns:
        {"artist", "title", "source"} при успехе, иначе None.
    """
    work_dir = tempfile.mkdtemp(dir=cache_mgr.cache_dir, prefix="work_")
    bitrate = 0
    try:
        if cand.source == "vk":
            final_mp3, cover_path = await _download_vk_media(
                cand.url, cand.cover_url, work_dir, cache_mgr, hash_key, ytdlp_proxy,
                cand.artist, cand.title,
            )
            if not final_mp3:
                return None
            disp_artist = (cand.artist or artist_hint or "Unknown Artist").strip()
            disp_title = (cand.title or "Unknown").strip()
            duration = int(cand.duration) or duration_hint
            source_url = cand.url
            if duration:
                try:
                    bitrate = int(os.path.getsize(final_mp3) * 8 / duration / 1000)
                except OSError:
                    bitrate = 0
        else:
            dr = await asyncio.to_thread(
                download_audio, cand.url, work_dir, "audio", ytdlp_proxy, cand.source,
            )
            if not dr or not dr.file_path:
                return None

            final_mp3 = cache_mgr._file_path(hash_key)
            if os.path.exists(final_mp3):
                try:
                    os.remove(final_mp3)
                except OSError:
                    pass
            os.replace(dr.file_path, final_mp3)

            disp_artist, disp_title = _resolve_display(dr, cand, artist_hint)
            duration = dr.duration or duration_hint
            source_url = dr.source_url
            bitrate = dr.bitrate

            cover_path = ""
            final_cover = cache_mgr._cover_path(hash_key)
            got_cover = False
            cover_origin = "none"
            # 1. VK-обложка: из плейлиста или найденная в VK при хорошем совпадении
            vk_cover = vk_cover_url or await asyncio.to_thread(_fetch_vk_cover, disp_artist, disp_title)
            if vk_cover:
                raw_vk = os.path.join(work_dir, "cover_vk")
                if await download_image(vk_cover, raw_vk, ""):
                    if make_thumbnail(raw_vk, final_cover):
                        cover_path = final_cover
                        got_cover = True
                        cover_origin = "vk"
            # 2. Обложка самого источника (SoundCloud/YouTube)
            if not got_cover and dr.cover_path:
                if make_thumbnail(dr.cover_path, final_cover):
                    cover_path = final_cover
                    cover_origin = "source"
            log.info("cover_resolved", track=disp_title[:40], origin=cover_origin)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    if display_override:
        disp_artist = (display_override[0] or disp_artist).strip() or disp_artist
        disp_title = (display_override[1] or disp_title).strip() or disp_title

    send_track = Track(artist=disp_artist, title=disp_title, duration=duration)
    redo_query = (
        f"{original_artist} {original_title}".strip()
        or query
        or f"{disp_artist} {disp_title}".strip()
    )
    redo_kb = _redo_keyboard(redo_query, channel_id, user_id)
    res = await sender.send_file(
        channel_id, final_mp3, send_track,
        cover=cover_path or None, reply_markup=redo_kb, caption=_build_caption(source_url),
    )

    cache_mgr.save(hash_key, {
        "artist": original_artist or query,
        "title": original_title or query,
        "display_artist": disp_artist,
        "display_title": disp_title,
        "file_path": final_mp3,
        "cover_path": cover_path,
        "telegram_file_id": res.get("file_id") or "",
        "cover_file_id": res.get("cover_file_id") or "",
        "source": cand.source,
        "source_url": source_url,
        "file_size": os.path.getsize(final_mp3),
        "downloaded_at": int(time.time()),
    })
    _bump(total_sent=1)
    return {"artist": disp_artist, "title": disp_title, "source": cand.source, "bitrate": bitrate}


async def search_all(
    query: str,
    source_order: List[str],
    ytdlp_proxy: str = "",
    per_source: int = 5,
) -> List[Candidate]:
    """Ищет во всех источниках параллельно, возвращает объединённый список."""

    async def one(src: str) -> List[Candidate]:
        searcher = _provider(src)
        if not searcher:
            return []
        cands = _cache_get(src, query)
        if cands is None:
            try:
                cands = await asyncio.to_thread(searcher, query, per_source, ytdlp_proxy)
            except Exception as e:
                log.warning("search_error", source=src, query=query, error=str(e))
                cands = []
            _cache_put(src, query, cands)
        return rank_candidates(cands, query, 0, "", limit=per_source)

    groups = await asyncio.gather(*(one(s) for s in source_order))
    return [c for group in groups for c in group]


def _effective_order(source_order: List[str], is_query: bool) -> List[str]:
    """Для свободного ввода/мешапов SoundCloud идёт первым (там оригиналы)."""
    if not is_query:
        return list(source_order)
    ordered = ["soundcloud"] + [s for s in source_order if s != "soundcloud"]
    return ordered


def _vk_find_track(track: Track) -> Optional[Track]:
    """Ищет трек в VK и возвращает Track с прямой ссылкой (или None).

    Используется в режиме «VK» для треков без готовой ссылки (напр. из альбома).
    """
    if not _vk_fetcher:
        return None
    artist_hint = clean_artist_for_search(track.artist)
    query = f"{artist_hint} {clean_title_for_search(track.title)}".strip() or track.title
    if not query:
        return None
    cands = _vk_search(query, 5)
    if not cands:
        return None
    best = max(cands, key=lambda c: score_candidate(c, query, track.duration, artist_hint))
    if artist_hint and score_candidate(best, query, track.duration, artist_hint) < MIN_MATCH_SCORE:
        log.info("vk_weak_match", query=query)
        return None
    return Track(
        artist=track.artist or best.artist,
        title=track.title or best.title,
        duration=track.duration or int(best.duration or 0),
        url=best.url,
        cover_url=track.cover_url or best.cover_url,
        source="vk",
        force_meta=track.force_meta,
    )


async def process_picked_candidate(
    user_id: int,
    channel_id: int,
    cand: Candidate,
    bot: Bot,
    cache_mgr: CacheManager,
    ytdlp_proxy: str = "",
    query: str = "",
    vk_cover_url: str = "",
) -> Optional[dict]:
    """Скачивает и отправляет выбранного пользователем кандидата (из пикера)."""
    sender = TelegramSender(bot)
    hash_key = generate_hash(f"pick:{query}".lower(), cand.url)
    _bump(total_tracks=1)
    info = await _download_and_send(
        cand, channel_id, cache_mgr, sender, ytdlp_proxy, hash_key, "", 0, "", "", query,
        vk_cover_url, user_id,
    )
    if not info:
        _bump(total_not_found=1)
    return info


async def _download_vk_and_send(
    track: Track,
    channel_id: int,
    cache_mgr: CacheManager,
    sender: TelegramSender,
    hash_key: str,
    ytdlp_proxy: str = "",
    user_id: int = 0,
    display_override: Optional[tuple[str, str]] = None,
) -> Optional[dict]:
    """Скачивает трек напрямую из VK (точная версия + обложка)."""
    work_dir = tempfile.mkdtemp(dir=cache_mgr.cache_dir, prefix="work_")
    try:
        final_mp3, cover_path = await _download_vk_media(
            track.url, track.cover_url, work_dir, cache_mgr, hash_key, ytdlp_proxy,
            track.artist, track.title,
        )
        if not final_mp3:
            return None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    disp_artist = track.artist.strip() or "Unknown Artist"
    disp_title = track.title.strip() or "Unknown"
    if display_override:
        disp_artist = (display_override[0] or disp_artist).strip() or disp_artist
        disp_title = (display_override[1] or disp_title).strip() or disp_title
    send_track = Track(artist=disp_artist, title=disp_title, duration=track.duration)
    bitrate = 0
    if track.duration:
        try:
            bitrate = int(os.path.getsize(final_mp3) * 8 / track.duration / 1000)
        except OSError:
            bitrate = 0

    redo_query = (
        track.query
        or f"{track.artist} {track.title}".strip()
        or f"{disp_artist} {disp_title}".strip()
    )
    redo_kb = _redo_keyboard(redo_query, channel_id, user_id)
    res = await sender.send_file(
        channel_id, final_mp3, send_track,
        cover=cover_path or None, reply_markup=redo_kb, caption=_build_caption(track.url),
    )
    cache_mgr.save(hash_key, {
        "artist": track.artist,
        "title": track.title,
        "display_artist": disp_artist,
        "display_title": disp_title,
        "file_path": final_mp3,
        "cover_path": cover_path,
        "telegram_file_id": res.get("file_id") or "",
        "cover_file_id": res.get("cover_file_id") or "",
        "source": "vk",
        "source_url": track.url,
        "file_size": os.path.getsize(final_mp3),
        "downloaded_at": int(time.time()),
    })
    _bump(total_sent=1)
    return {"artist": disp_artist, "title": disp_title, "source": "vk", "bitrate": bitrate}


async def _process_single_track(
    track: Track,
    channel_id: int,
    cache_mgr: CacheManager,
    sender: TelegramSender,
    source_order: List[str],
    ytdlp_proxy: str = "",
    mode: str = "uncensored",
    user_id: int = 0,
) -> str:
    hash_key = generate_hash(track.artist or track.query or "", track.title)
    vk_available = track.source == "vk" and bool(track.url)
    _bump(total_tracks=1)

    # --- кэш ---
    cached = cache_mgr.get(hash_key)
    if cached:
        file_id = cached.get("telegram_file_id")
        file_path = cached.get("file_path", "")
        cover = cached.get("cover_path") or ""
        disp_artist = cached.get("display_artist") or track.artist
        disp_title = cached.get("display_title") or track.title
        if track.force_meta:
            disp_artist = track.artist or disp_artist
            disp_title = track.title or disp_title
        send_track = Track(
            artist=disp_artist,
            title=disp_title,
            duration=track.duration,
        )
        redo_query = (
            track.query
            or f"{track.artist} {track.title}".strip()
            or f"{disp_artist} {disp_title}".strip()
        )
        redo_kb = _redo_keyboard(redo_query, channel_id, user_id)
        caption = _build_caption(cached.get("source_url", ""))
        if file_id:
            try:
                res = await sender.send_by_file_id(channel_id, file_id, send_track, cover=cover, reply_markup=redo_kb, caption=caption)
                if res.get("cover_file_id") and not cached.get("cover_file_id"):
                    cache_mgr.save_cover_file_id(hash_key, res["cover_file_id"])
                cache_mgr.update_access(hash_key)
                _bump(total_sent=1, total_cached=1)
                return "sent_cached_file_id"
            except Exception:
                pass
        if file_path and os.path.exists(file_path):
            res = await sender.send_file(channel_id, file_path, send_track, cover=cover, reply_markup=redo_kb, caption=caption)
            if res.get("file_id"):
                cache_mgr.save_file_id(hash_key, res["file_id"])
            if res.get("cover_file_id"):
                cache_mgr.save_cover_file_id(hash_key, res["cover_file_id"])
            _bump(total_sent=1, total_cached=1)
            return "sent_cached_file"

    # --- VK: режим «Всё из VK» ---
    if mode == "vk":
        vk_track: Optional[Track] = track if vk_available else None
        if vk_track is None:
            vk_track = await asyncio.to_thread(_vk_find_track, track)
        if vk_track and vk_track.url:
            info = await _download_vk_and_send(
                vk_track, channel_id, cache_mgr, sender, hash_key, ytdlp_proxy, user_id,
                (track.artist, track.title) if track.force_meta else None,
            )
            if info:
                log.info("track_sent", source="vk", display=f"{info['artist']} - {info['title']}")
                return "sent_new"
        log.warning("vk_direct_failed_fallback_search", artist=track.artist, title=track.title)

    # --- поиск по источникам ---
    if track.query:
        query = track.query
        artist_hint = ""
    else:
        artist_hint = clean_artist_for_search(track.artist)
        query = f"{artist_hint} {clean_title_for_search(track.title)}".strip()

    order = _effective_order(source_order, bool(track.query) or is_mashup(track.title) or is_mashup(track.artist))

    try:
        for source_name in order:
            searcher = _provider(source_name)
            if not searcher:
                continue

            candidates = await asyncio.to_thread(searcher, query, 5, ytdlp_proxy)
            ranked = rank_candidates(candidates, query, track.duration, artist_hint, limit=3)
            if not ranked:
                continue

            # Отсекаем заведомо слабые совпадения (структурные треки с артистом)
            if artist_hint and score_candidate(ranked[0], query, track.duration, artist_hint) < MIN_MATCH_SCORE:
                log.info("source_weak_match", source=source_name, query=query)
                continue

            for cand in ranked:
                info = await _download_and_send(
                    cand, channel_id, cache_mgr, sender, ytdlp_proxy, hash_key,
                    artist_hint, track.duration, track.artist, track.title, query,
                    track.cover_url, user_id,
                    (track.artist, track.title) if track.force_meta else None,
                )
                if info:
                    log.info("track_sent", source=info["source"], display=f"{info['artist']} - {info['title']}")
                    return "sent_new"

        # Fallback: ничего не нашли в uncensored-источниках → отдаём версию из VK
        if mode != "vk" and vk_available:
            info = await _download_vk_and_send(
                track, channel_id, cache_mgr, sender, hash_key, ytdlp_proxy, user_id,
                (track.artist, track.title) if track.force_meta else None,
            )
            if info:
                log.info("track_sent_vk_fallback", display=f"{info['artist']} - {info['title']}")
                return "sent_new"

        log.info("track_not_found", artist=track.artist, title=track.title, query=query)
        _bump(total_not_found=1)
        return "not_found"

    except Exception as e:
        log.error("track_processing_error", artist=track.artist, title=track.title, error=str(e))
        return "error"
