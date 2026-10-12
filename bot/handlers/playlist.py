"""Хендлер приёма плейлиста (.txt / текст / VK-ссылка) и запуска обработки.

Поддерживается:
  - .txt файл (первая строка может быть chat_id)
  - текст: первая строка может быть chat_id, далее треки
  - трек "Артист - Название [сек]" и свободный запрос (без тире)
  - ссылка на плейлист ВК (нужен VK_TOKEN) с выбором режима
"""

import asyncio
import os
import tempfile
import time
import uuid
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.cache_manager import CacheManager
from core.scraper import youtube
from core.scraper.common import Candidate, split_artist_title
from core.vk_fetcher import VKFetcher
from services.processor import (
    get_redo,
    is_user_busy,
    process_picked_candidate,
    process_playlist,
    search_all,
)
from utils.escape import esc_html, esc_md
from utils.helpers import (
    Track,
    clean_artist_for_search,
    clean_title_for_search,
    parse_track_line,
)
from utils.links import find_youtube_link
from utils.logger import get_logger
from utils.rich import edit_rich
from utils.storage import UserStorage
from utils.ui import fmt_duration_mmss, is_mashup, source_icon, version_tag
from utils.vk_link import find_vk_link, parse_vk_link, parse_vk_track_link

log = get_logger("playlist_handler")

router = Router()

_cache_mgr: CacheManager | None = None
_storage: UserStorage | None = None
_max_playlist_size: int = 100
_rate_limit: float = 2.0
_source_order: list[str] = ["youtube", "soundcloud"]
_ytdlp_proxy: str = ""
_vk_token: str = ""
_vk_playlist_mode: str = "ask"
_concurrency: int = 3

# Интерактивный выбор результатов
_pending_picks: dict[str, dict] = {}
_PICK_TTL = 20 * 60
_PICK_PAGE_SIZE = 8
_PICK_MAX = 40

# Меню режима для VK-плейлиста
_vk_menu_pending: dict[str, dict] = {}

# Поиск по тексту песни: token -> {user_id, channel_id, text, songs, created}
_lyric_pending: dict[str, dict] = {}

# Сессии «Выбирать каждый трек»: user_id -> {...}
_pick_sessions: dict[int, dict] = {}

_FILTERS = ("all", "youtube", "soundcloud", "vk")


def set_dependencies(
    cache_mgr: CacheManager,
    storage: UserStorage,
    max_playlist_size: int = 100,
    rate_limit: float = 2.0,
    source_order: list[str] | None = None,
    ytdlp_proxy: str = "",
    vk_token: str = "",
    vk_playlist_mode: str = "ask",
    concurrency: int = 3,
) -> None:
    """Устанавливает зависимости (вызывается при инициализации бота)."""
    global _cache_mgr, _storage, _max_playlist_size, _rate_limit
    global _source_order, _ytdlp_proxy, _vk_token, _vk_playlist_mode, _concurrency
    _cache_mgr = cache_mgr
    _storage = storage
    _max_playlist_size = max_playlist_size
    _rate_limit = rate_limit
    _source_order = source_order or ["youtube", "soundcloud"]
    _ytdlp_proxy = ytdlp_proxy
    _vk_token = vk_token
    _vk_playlist_mode = (vk_playlist_mode or "ask").lower()
    _concurrency = max(1, concurrency)


# ----------------------------- утилиты / парсинг -----------------------------

@dataclass
class ParsedPlaylist:
    name: str | None
    channel_id: int | None
    tracks: list[Track]
    total_duration: int


def _is_valid_channel_id(channel_id: int) -> bool:
    return abs(channel_id) >= 100


def _parse_playlist(text: str) -> ParsedPlaylist:
    """Парсит текст плейлиста: chat_id + треки."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    channel_id: int | None = None
    tracks: list[Track] = []

    for i, line in enumerate(lines):
        if i == 0 and line.lstrip("-").isdigit() and len(line) > 3:
            try:
                cid = int(line)
                if abs(cid) >= 100:
                    channel_id = cid
                    continue
            except ValueError:
                pass
        track = parse_track_line(line)
        if track:
            tracks.append(track)

    total_duration = sum(t.duration for t in tracks if t.duration > 0)
    return ParsedPlaylist(name=None, channel_id=channel_id, tracks=tracks, total_duration=total_duration)


def _resolve_channel_id(text: str | None, user_id: int) -> int | None:
    if text:
        parts = text.strip().split(maxsplit=1)
        if parts:
            try:
                cid = int(parts[0])
                if _is_valid_channel_id(cid):
                    return cid
            except ValueError:
                pass
    default = _storage.get_default_channel(user_id)
    if default and _is_valid_channel_id(default):
        return default
    return None


def _resolve_target_channel(message: Message, channel_hint: str | None) -> int | None:
    user_id = message.from_user.id
    if channel_hint:
        cid = _resolve_channel_id(channel_hint, user_id)
        if cid is not None:
            return cid
    return _storage.get_default_channel(user_id)


async def _fetch_vk_playlist(ref) -> list[Track]:
    fetcher = VKFetcher(_vk_token)
    return await fetcher.get_playlist_tracks_async(
        ref.owner_id, ref.playlist_id, ref.access_key, count=_max_playlist_size,
    )


async def _fetch_vk_track(ref) -> Track | None:
    fetcher = VKFetcher(_vk_token)
    return await fetcher.get_track_async(ref.owner_id, ref.audio_id, ref.access_key)


# кнопка «найти варианты» после прямой ссылки: token -> {query, channel_id, user_id}
_reopen_pending: dict[str, dict] = {}


async def _send_direct(message: Message, cand: Candidate, label: str, user_id: int | None = None) -> None:
    """Скачивает и отправляет конкретный трек (по прямой ссылке) + кнопка вариантов."""
    user_id = user_id or message.from_user.id
    channel_id = _resolve_target_channel(message, None)
    if channel_id is None:
        await message.answer(
            "❌ <b>Канал не определён</b>\n\nПривяжите канал: <code>/link -100XXXXXXXXXX</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not _storage.has_channel(user_id, channel_id):
        _storage.add_channel(user_id, channel_id)

    status = await message.answer(f"⏳ Скачиваю: <i>{label[:60]}</i>...", parse_mode=ParseMode.HTML)
    info = await process_picked_candidate(
        user_id=user_id, channel_id=channel_id, cand=cand,
        bot=message.bot, cache_mgr=_cache_mgr, ytdlp_proxy=_ytdlp_proxy, query=label,
    )
    if not info:
        await status.edit_text("❌ Не удалось скачать этот трек.")
        return

    # кнопка «найти варианты» по запросу
    query = f"{info['artist']} {info['title']}".strip()
    token = uuid.uuid4().hex[:8]
    _reopen_pending[token] = {"query": query, "channel_id": channel_id, "user_id": user_id}
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔁 Найти варианты", callback_data=f"reopen|{token}"),
    ]])
    kbps = f" · {info['bitrate']} kbps" if info.get("bitrate") else ""
    await status.edit_text(
        f"✅ Отправлено в канал:\n<b>{esc_html(info['artist'])} — {esc_html(info['title'])}</b>{kbps}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def _handle_vk_track(message: Message, ref) -> None:
    """Обрабатывает VK-ссылку на одиночный трек."""
    if not _vk_token:
        await message.answer("❌ Нужен <b>VK_TOKEN</b> для ссылок ВК.", parse_mode=ParseMode.HTML)
        return
    status = await message.answer("⏳ Читаю трек ВКонтакте...")
    try:
        track = await _fetch_vk_track(ref)
    except Exception as e:
        log.error("vk_track_fetch_failed", error=str(e))
        await status.edit_text("❌ Не удалось получить трек.")
        return
    if not track or not track.url:
        await status.edit_text("❌ Трек недоступен (нет ссылки на аудио).")
        return
    try:
        await status.delete()
    except Exception:
        pass
    cand = Candidate(url=track.url, title=track.title, artist=track.artist,
                     uploader=track.artist, duration=track.duration or 0,
                     cover_url=track.cover_url, source="vk")
    await _send_direct(message, cand, f"{track.artist} — {track.title}")


async def _handle_youtube_link(message: Message, url: str) -> None:
    """Обрабатывает ссылку YouTube — качаем конкретный ролик."""
    cand = Candidate(url=url, title="", artist="", source="youtube")
    await _send_direct(message, cand, "YouTube")


# ----------------------------- пикер ----------------------------------------

def _cleanup_picks() -> None:
    now = time.time()
    for tok in [t for t, v in _pending_picks.items() if now - v["created"] > _PICK_TTL]:
        _pending_picks.pop(tok, None)
    for tok in [t for t, v in _vk_menu_pending.items() if now - v["created"] > _PICK_TTL]:
        _vk_menu_pending.pop(tok, None)
    for tok in [t for t, v in _lyric_pending.items() if now - v["created"] > _PICK_TTL]:
        _lyric_pending.pop(tok, None)


_FILTER_LABEL = {"all": "🔀 Все", "youtube": "▶️ YouTube", "soundcloud": "☁️ SoundCloud", "vk": "VK"}
_FILTER_SHORT = {"all": "🔀", "youtube": "▶️ YT", "soundcloud": "☁️ SC", "vk": "VK"}


async def _search_candidates(
    query: str,
    extra_exact: Candidate | None = None,
    per_source: int = 5,
    sources: list[str] | None = None,
):
    """Ищет в источниках. Возвращает (all, filters).

    `extra_exact` — точный VK-трек (напр. из плейлиста), показывается в VK-вкладке
    без поиска по VK. Если `sources` не задан — ищем SC+YT+VK.
    """
    sources = sources if sources is not None else ["soundcloud", "youtube", "vk"]
    results = await search_all(query, sources, _ytdlp_proxy, per_source=per_source)
    all_c: list[Candidate] = []
    filters: dict[str, list[int]] = {"youtube": [], "soundcloud": [], "vk": []}
    first_idx: dict[str, int] = {}

    def add(c: Candidate):
        gi = len(all_c)
        filters.setdefault(c.source, []).append(gi)
        first_idx.setdefault(c.source, gi)
        all_c.append(c)

    for c in results:
        add(c)
    if extra_exact is not None and extra_exact.url:
        add(extra_exact)

    # «Все» = лучший (первый) кандидат каждой платформы, в порядке появления
    order = list(dict.fromkeys(c.source for c in all_c))
    filters["all"] = [first_idx[s] for s in order]
    return all_c[:_PICK_MAX], {k: [i for i in v if i < _PICK_MAX] for k, v in filters.items()}


def _picker_text(data: dict, page: int) -> str:
    filt = data.get("filter", "all")
    idxs = data["filters"].get(filt, [])
    total_pages = max(1, (len(idxs) + _PICK_PAGE_SIZE - 1) // _PICK_PAGE_SIZE)
    title = data.get("title")
    title_html = f"<b>{esc_html(title)}</b>" if title else f"🔍 <i>{esc_html(data['query'])}</i>"
    return (
        f"🎧 <b>Результаты</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{title_html}\n"
        f"Найдено: <b>{len(idxs)}</b>  ·  фильтр: {_FILTER_LABEL.get(filt, filt)}\n"
        f"Страница <b>{page + 1}</b>/{total_pages}\n\n"
        f"Выбери трек 👇"
    )


def _picker_md(data: dict, page: int) -> str:
    filt = data.get("filter", "all")
    idxs = data["filters"].get(filt, [])
    total_pages = max(1, (len(idxs) + _PICK_PAGE_SIZE - 1) // _PICK_PAGE_SIZE)
    title = data.get("title")
    title_md = f"**{esc_md(title)}**" if title else f"🔍 **{esc_md(data['query'])}**"
    return (
        f"# 🎧 Результаты\n\n"
        f"{title_md}\n\n"
        f"Найдено: **{len(idxs)}**  ·  фильтр: {_FILTER_LABEL.get(filt, filt)}\n\n"
        f"Страница **{page + 1}**/{total_pages}\n\n"
        f"Выбери трек 👇"
    )


def _picker_keyboard(token: str, data: dict, page: int) -> InlineKeyboardMarkup:
    filt = data.get("filter", "all")
    idxs = data["filters"].get(filt, [])
    start = page * _PICK_PAGE_SIZE
    page_idx = idxs[start:start + _PICK_PAGE_SIZE]
    total_pages = max(1, (len(idxs) + _PICK_PAGE_SIZE - 1) // _PICK_PAGE_SIZE)

    rows = []
    for j, gi in enumerate(page_idx):
        c = data["all"][gi]
        icon = source_icon(c.source)
        tag = version_tag(c.title)
        artist = c.artist or c.uploader or ""
        prefix = " ".join(x for x in (icon, tag) if x)
        label = f"{start + j + 1}. " + (f"{prefix} " if prefix else "") + c.title
        if artist:
            label += f" — {artist}"
        label += f" · {fmt_duration_mmss(c.duration)}"
        if len(label) > 60:
            label = label[:59] + "…"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"pick|{token}|{gi}")])

    # фильтр источников (активный — зелёный)
    frow = []
    for f in _FILTERS:
        if not data["filters"].get(f):
            continue
        frow.append(InlineKeyboardButton(
            text=_FILTER_SHORT[f],
            callback_data=f"pickfilter|{token}|{f}",
            style="success" if f == filt else None,
        ))
    if frow:
        rows.append(frow)

    # пагинация
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"pickpage|{token}|{filt}|{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"· {page + 1}/{total_pages} ·", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"pickpage|{token}|{filt}|{page + 1}"))
        rows.append(nav)

    # режим «выбирать каждый»
    if data.get("pk_session") is not None:
        rows.append([
            InlineKeyboardButton(text="✅ Авто из VK", callback_data=f"pk_auto|{token}", style="success"),
            InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"pk_skip|{token}"),
        ])

    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"pickcancel|{token}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_picker(message: Message, token: str, data: dict, page: int) -> None:
    await edit_rich(
        message.bot, message.chat.id, message.message_id,
        _picker_md(data, page),
        reply_markup=_picker_keyboard(token, data, page),
        fallback=_picker_text(data, page),
    )


async def _show_picker(message: Message, query: str, channel_id: int, user_id: int | None = None) -> None:
    """Одиночный запрос: поиск + показ пикера."""
    user_id = user_id or message.from_user.id
    if is_user_busy(user_id):
        await message.answer("⏳ У вас уже выполняется задача. Дождитесь завершения или /cancel.")
        return

    await _picker_flow(message.bot, message.chat.id, user_id, query, channel_id)


async def _picker_flow(bot, chat_id: int, user_id: int, query: str, channel_id: int, note: str | None = None) -> None:
    """Поиск и показ пикера в указанный чат (ЛС)."""
    _cleanup_picks()
    status = await bot.send_message(chat_id, f"🔎 Ищу: <i>{esc_html(query)}</i>...", parse_mode=ParseMode.HTML)
    try:
        all_c, filters = await _search_candidates(query)
    except Exception as e:
        log.error("picker_search_error", query=query, error=str(e))
        all_c, filters = [], {}

    if not all_c:
        await status.edit_text(f"❌ По запросу «{esc_html(query)}» ничего не найдено.", parse_mode=ParseMode.HTML)
        return

    token = uuid.uuid4().hex[:8]
    data = {
        "user_id": user_id,
        "channel_id": channel_id,
        "query": query,
        "title": note,
        "created": time.time(),
        "all": all_c,
        "filters": filters,
        "filter": "all",
        "page": 0,
        "pk_session": None,
        "pk_index": 0,
    }
    _pending_picks[token] = data
    await _render_picker(status, token, data, 0)


def _should_resolve_lyrics(line: str) -> bool:
    """Похоже ли на строку из текста песни (≥3 слов, не мешап, не ссылка, без тире)."""
    line = (line or "").strip()
    if not line or "http" in line.lower():
        return False
    if " - " in line or " – " in line or " — " in line:
        return False
    if is_mashup(line):
        return False
    return len(line.split()) >= 3


async def _show_lyric_stage(message: Message, text: str, channel_id: int, user_id: int | None = None) -> None:
    """Стадия 1: ищем по тексту в YouTube и предлагаем распознанные песни."""
    user_id = user_id or message.from_user.id
    if is_user_busy(user_id):
        await message.answer("⏳ У вас уже выполняется задача. Дождитесь завершения или /cancel.")
        return

    _cleanup_picks()
    status = await message.answer(f"🎤 Ищу по тексту: <i>{esc_html(text)}</i>...", parse_mode=ParseMode.HTML)
    try:
        cands = await asyncio.to_thread(youtube.search, text, 5, _ytdlp_proxy)
    except Exception as e:
        log.error("lyric_search_error", error=str(e))
        cands = []

    songs: list[tuple[str, str]] = []
    seen: set = set()
    for c in cands:
        artist, title = split_artist_title(c.title, c.uploader)
        artist = (artist or c.uploader or "").strip()
        title = (title or "").strip()
        if not title:
            continue
        key = (artist.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        songs.append((artist, title))

    if not songs:
        try:
            await status.delete()
        except Exception:
            pass
        await _picker_flow(message.bot, message.chat.id, user_id, text, channel_id)
        return

    songs = songs[:5]
    token = uuid.uuid4().hex[:8]
    _lyric_pending[token] = {
        "user_id": user_id,
        "channel_id": channel_id,
        "text": text,
        "songs": songs,
        "created": time.time(),
    }
    rows = []
    for i, (artist, title) in enumerate(songs):
        label = f"{artist} — {title}" if artist else title
        if len(label) > 60:
            label = label[:59] + "…"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"lyricpick|{token}|{i}")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"pickcancel|{token}", style="danger")])
    await status.edit_text(
        f"🎤 <b>Похоже, это песня:</b>\n<i>{esc_html(text)}</i>\n\nВыбери трек 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("lyricpick|"))
async def cb_lyric_pick(callback: CallbackQuery):
    """Стадия 2: пользователь подтвердил песню — ищем её по всем источникам."""
    try:
        _, token, idx = callback.data.split("|")
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    data = _lyric_pending.pop(token, None)
    if not data:
        await callback.answer("⌛ Устарело — пришлите текст заново.", show_alert=True)
        return
    if callback.from_user.id != data["user_id"]:
        await callback.answer("Это не ваш поиск.", show_alert=True)
        return

    artist, title = data["songs"][int(idx)]
    resolved = f"{artist} {title}".strip() or data["text"]
    note = f"🎤 по тексту → {artist} — {title}" if artist else f"🎤 по тексту → {title}"
    await callback.answer("🔎 Ищу трек…")
    try:
        await callback.message.edit_text(f"🎤 <b>{esc_html(artist)} — {esc_html(title)}</b>\n🔎 Ищу...", parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await _picker_flow(callback.bot, data["user_id"], data["user_id"], resolved, data["channel_id"], note=note)


# ----------------------------- запуск задачи --------------------------------

async def _start_processing(
    message: Message,
    raw_text: str,
    channel_hint: str | None = None,
    playlist_name_override: str | None = None,
    tracks_override: list[Track] | None = None,
    mode: str = "uncensored",
    user_id: int | None = None,
):
    """Парсинг треков, определение канала, запуск задачи."""
    user_id = user_id or message.from_user.id

    parsed = _parse_playlist(raw_text)
    if playlist_name_override:
        parsed.name = playlist_name_override

    tracks = tracks_override if tracks_override is not None else parsed.tracks
    total_duration = parsed.total_duration
    if tracks_override is not None:
        total_duration = sum(t.duration for t in tracks if t.duration > 0)

    if not tracks:
        await message.answer(
            "❌ <b>Треки не найдены</b>\n\n"
            "Отправьте список, где каждая строка — трек.\n"
            "Форматы: <code>Исполнитель - Название</code> или свободный запрос.",
            parse_mode=ParseMode.HTML,
        )
        return

    if len(tracks) > _max_playlist_size:
        tracks = tracks[:_max_playlist_size]
        await message.answer(f"⚠️ Больше {_max_playlist_size} треков. Обработаю первые {len(tracks)}.")

    if is_user_busy(user_id):
        await message.answer(
            "⏳ <b>Задача уже выполняется</b>\n\nДождитесь завершения или /cancel.",
            parse_mode=ParseMode.HTML,
        )
        return

    channel_id = parsed.channel_id
    if channel_id is None and channel_hint:
        channel_id = _resolve_channel_id(channel_hint, user_id)
    if channel_id is None:
        channel_id = _storage.get_default_channel(user_id)

    if channel_id is None:
        await message.answer(
            "❌ <b>Канал не определён</b>\n\nПривяжите канал: <code>/link -100XXXXXXXXXX</code>",
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
        total_duration=total_duration,
        bot=message.bot,
        cache_mgr=_cache_mgr,
        rate_limit=_rate_limit,
        source_order=_source_order,
        ytdlp_proxy=_ytdlp_proxy,
        mode=mode,
        concurrency=_concurrency,
    )
    log.info("playlist_task_created", task_id=task_id, user_id=user_id, channel_id=channel_id, tracks=len(tracks), mode=mode)


# ----------------------------- приём ввода ----------------------------------

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
    if not all([_cache_mgr, _storage]):
        await message.answer("⚠️ Бот не полностью инициализирован. Попробуйте позже.")
        return

    playlist_name = None
    if message.caption:
        playlist_name = message.caption.strip()
    if not playlist_name:
        playlist_name = os.path.splitext(document.file_name)[0].strip() or None

    tmp_path = None
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
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if find_vk_link(content):
        vk_link = find_vk_link(content)
        track_ref = parse_vk_track_link(vk_link)
        if track_ref:
            await _handle_vk_track(message, track_ref)
            return
        await _handle_vk_link(message, content, playlist_name)
        return

    yt = find_youtube_link(content)
    if yt:
        await _handle_youtube_link(message, yt)
        return

    await _start_processing(message, raw_text=content, playlist_name_override=playlist_name)


@router.message(F.chat.type == "private", F.text & ~F.text.startswith("/"))
async def handle_playlist_text(message: Message):
    """Текст: плейлист, одиночный запрос или VK-ссылка."""
    text = message.text or ""
    if not all([_cache_mgr, _storage]):
        await message.answer("⚠️ Бот не полностью инициализирован. Попробуйте позже.")
        return

    if find_vk_link(text):
        vk_link = find_vk_link(text)
        track_ref = parse_vk_track_link(vk_link)
        if track_ref:
            await _handle_vk_track(message, track_ref)
            return
        if parse_vk_link(vk_link):
            await _handle_vk_link(message, text)
            return

    yt = find_youtube_link(text)
    if yt:
        await _handle_youtube_link(message, yt)
        return

    lines = text.splitlines()
    channel_hint = lines[0] if lines else None
    if channel_hint and channel_hint.strip().lstrip("-").isdigit():
        raw_text = "\n".join(lines[1:])
    else:
        raw_text = text
        channel_hint = None

    track_lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip() and not ln.strip().startswith("#")]

    if len(track_lines) == 1:
        channel_id = _resolve_target_channel(message, channel_hint)
        if channel_id is None:
            await message.answer(
                "❌ <b>Канал не определён</b>\n\nПривяжите канал: <code>/link -100XXXXXXXXXX</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        if not _storage.has_channel(message.from_user.id, channel_id):
            _storage.add_channel(message.from_user.id, channel_id)
        if _should_resolve_lyrics(track_lines[0]):
            await _show_lyric_stage(message, track_lines[0], channel_id)
        else:
            await _show_picker(message, track_lines[0], channel_id)
        return

    await _start_processing(message, raw_text, channel_hint=channel_hint)


# ----------------------------- VK-плейлист ----------------------------------

async def _handle_vk_link(message: Message, text: str, playlist_name: str | None = None) -> None:
    """Обрабатывает VK-ссылку: грузит треки и предлагает режим."""
    ref = parse_vk_link(text)
    if not ref:
        await message.answer("❌ Не удалось разобрать ссылку ВК.")
        return

    if not _vk_token:
        await message.answer(
            "❌ Для загрузки плейлистов по ссылке нужен <b>VK_TOKEN</b>.\n\n"
            "Добавьте его в <code>.env</code> (инструкция в README).",
            parse_mode=ParseMode.HTML,
        )
        return

    channel_id = _resolve_target_channel(message, None)
    if channel_id is None:
        await message.answer(
            "❌ <b>Канал не определён</b>\n\nПривяжите канал: <code>/link -100XXXXXXXXXX</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not _storage.has_channel(message.from_user.id, channel_id):
        _storage.add_channel(message.from_user.id, channel_id)

    status = await message.answer("⏳ Читаю плейлист ВКонтакте...")
    try:
        tracks = await _fetch_vk_playlist(ref)
    except Exception as e:
        log.error("vk_fetch_failed", error=str(e))
        await status.edit_text(f"❌ Не удалось получить плейлист: {str(e)[:150]}")
        return

    if not tracks:
        await status.edit_text("❌ Плейлист пуст или недоступен.")
        return

    if len(tracks) > _max_playlist_size:
        tracks = tracks[:_max_playlist_size]

    name = playlist_name or f"Плейлист ВК ({len(tracks)} треков)"

    # Режим по умолчанию (не "ask")
    if _vk_playlist_mode in ("uncensored", "vk"):
        await status.edit_text(f"✅ Найдено {len(tracks)} треков. Режим: {_vk_playlist_mode}.")
        await _start_processing(message, raw_text="", playlist_name_override=name,
                                tracks_override=tracks, mode=_vk_playlist_mode)
        return

    # "ask" → меню
    token = uuid.uuid4().hex[:8]
    _vk_menu_pending[token] = {
        "user_id": message.from_user.id,
        "channel_id": channel_id,
        "tracks": tracks,
        "name": name,
        "created": time.time(),
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Искать оригиналы (uncensored)", callback_data=f"vkmode|{token}|uncensored", style="primary")],
        [InlineKeyboardButton(text="⬇️ Всё из VK", callback_data=f"vkmode|{token}|vk")],
        [InlineKeyboardButton(text="🎛 Выбирать каждый", callback_data=f"vkmode|{token}|pick")],
    ])
    await status.edit_text(
        f"🎧 <b>{name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎵 Треков: <b>{len(tracks)}</b>\n\n"
        f"<b>Как обрабатывать?</b>\n"
        f"🔍 искать оригиналы · ⬇️ точные версии из VK · 🎛 выбрать каждый",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("vkmode|"))
async def cb_vk_mode(callback: CallbackQuery):
    """Выбор режима обработки VK-плейлиста."""
    try:
        _, token, mode = callback.data.split("|")
    except ValueError:
        await callback.answer()
        return

    data = _vk_menu_pending.pop(token, None)
    if not data:
        await callback.answer("⌛ Меню устарело — пришлите ссылку заново.", show_alert=True)
        return
    if callback.from_user.id != data["user_id"]:
        await callback.answer("Это не ваш плейлист.", show_alert=True)
        return

    name = data["name"]
    tracks = data["tracks"]

    if is_user_busy(callback.from_user.id):
        await callback.answer("⏳ Уже выполняется задача. Дождитесь или /cancel.", show_alert=True)
        return

    await callback.answer()

    if mode in ("uncensored", "vk"):
        label = "🔍 Искать оригиналы" if mode == "uncensored" else "⬇️ Всё из VK"
        try:
            await callback.message.edit_text(f"✅ {label}: {len(tracks)} треков. Запускаю...")
        except Exception:
            pass
        await _start_processing(callback.message, raw_text="", playlist_name_override=name,
                                tracks_override=tracks, channel_hint=str(data["channel_id"]), mode=mode,
                                user_id=callback.from_user.id)
        return

    # pick — по каждому треку
    _pick_sessions[callback.from_user.id] = {
        "tracks": tracks,
        "index": 0,
        "channel_id": data["channel_id"],
        "name": name,
        "created": time.time(),
    }
    try:
        await callback.message.edit_text("🎛 Режим выбора по трекам. Поехали 👇")
    except Exception:
        pass
    await _vk_choose_send(callback.message, callback.from_user.id)


async def _vk_choose_send(base_message: Message, user_id: int) -> None:
    """Показывает пикер для следующего трека в режиме «выбирать каждый»."""
    session = _pick_sessions.get(user_id)
    if not session:
        return

    i = session["index"]
    tracks = session["tracks"]
    if i >= len(tracks):
        _pick_sessions.pop(user_id, None)
        await base_message.answer("✅ Выбор завершён!")
        return

    track = tracks[i]
    query = f"{clean_artist_for_search(track.artist)} {clean_title_for_search(track.title)}".strip() \
        or track.title or "Unknown"
    extra_exact = None
    if track.url:
        extra_exact = Candidate(url=track.url, title=track.title, artist=track.artist,
                                uploader=track.artist, duration=track.duration or 0,
                                cover_url=track.cover_url, source="vk")

    status = await base_message.answer(
        f"🎛 Трек {i + 1}/{len(tracks)}: <b>{esc_html(track.artist)} — {esc_html(track.title)}</b>\n🔎 Ищу...",
        parse_mode=ParseMode.HTML,
    )
    try:
        # VK-вкладка = точный трек из плейлиста (без поиска по VK)
        all_c, filters = await _search_candidates(
            query, extra_exact=extra_exact, sources=["soundcloud", "youtube"],
        )
    except Exception as e:
        log.error("vk_choose_search_error", error=str(e))
        all_c, filters = [], {}

    if not all_c:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"pk_skip_track|{user_id}")],
        ])
        await status.edit_text(
            f"❌ Ничего не найдено для <b>{esc_html(track.artist)} — {esc_html(track.title)}</b>.",
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return

    token = uuid.uuid4().hex[:8]
    _pending_picks[token] = {
        "user_id": user_id,
        "channel_id": session["channel_id"],
        "query": query,
        "title": f"🎛 {i + 1}/{len(tracks)} · {track.artist} — {track.title}",
        "created": time.time(),
        "all": all_c,
        "filters": filters,
        "filter": "vk" if filters.get("vk") else "all",
        "page": 0,
        "pk_session": user_id,
        "pk_index": i,
        "vk_cover_url": track.cover_url,
    }
    await _render_picker(status, token, _pending_picks[token], 0)


async def _advance_choose(user_id: int, new_index: int) -> None:
    session = _pick_sessions.get(user_id)
    if session:
        session["index"] = new_index


async def _continue_choose(base_message: Message, user_id: int, next_index: int) -> None:
    await _advance_choose(user_id, next_index)
    await _vk_choose_send(base_message, user_id)


# ----------------------------- callbacks: пикер -----------------------------

@router.callback_query(F.data.startswith("pick|"))
async def cb_pick(callback: CallbackQuery):
    """Пользователь выбрал трек."""
    try:
        _, token, gi = callback.data.split("|")
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    data = _pending_picks.get(token)
    if not data:
        await callback.answer("⌛ Результаты устарели — поищите заново.", show_alert=True)
        return
    if callback.from_user.id != data["user_id"]:
        await callback.answer("Это не ваш поиск.", show_alert=True)
        return

    cand = data["all"][int(gi)]
    await callback.answer("⏳ Скачиваю...")
    try:
        await callback.message.edit_text(f"⏳ Скачиваю: <i>{cand.title[:60]}</i>...", parse_mode=ParseMode.HTML)
    except Exception:
        pass

    info = await process_picked_candidate(
        user_id=data["user_id"],
        channel_id=data["channel_id"],
        cand=cand,
        bot=callback.message.bot,
        cache_mgr=_cache_mgr,
        ytdlp_proxy=_ytdlp_proxy,
        query=data["query"],
        vk_cover_url=data.get("vk_cover_url", ""),
    )

    # авто-фолбэк: если выбранный вариант не скачался — пробуем другие (другой источник первым)
    if not info:
        alternatives = [c for c in data["all"] if c.url != cand.url]
        alternatives.sort(key=lambda c: (c.source == cand.source,))
        for alt in alternatives[:3]:
            try:
                await callback.message.edit_text(
                    f"⏳ Не вышло, пробую {source_icon(alt.source)} <b>{esc_html(alt.title[:50])}</b>...",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            info = await process_picked_candidate(
                user_id=data["user_id"],
                channel_id=data["channel_id"],
                cand=alt,
                bot=callback.message.bot,
                cache_mgr=_cache_mgr,
                ytdlp_proxy=_ytdlp_proxy,
                query=data["query"],
                vk_cover_url=data.get("vk_cover_url", ""),
            )
            if info:
                break

    if not info:
        # даём выбрать другой — пикер оставляем, отдельно сообщаем об ошибке
        await _render_picker(callback.message, token, data, data.get("page", 0))
        try:
            await callback.message.answer("❌ Не удалось скачать ни один вариант.")
        except Exception:
            pass
        return

    kbps = f" · {info['bitrate']} kbps" if info.get("bitrate") else ""
    success_text = f"✅ Отправлено в канал:\n<b>{esc_html(info['artist'])} — {esc_html(info['title'])}</b>{kbps}"

    if data.get("pk_session") is not None:
        session = _pick_sessions.get(data["pk_session"])
        total = len(session["tracks"]) if session else 0
        done = data["pk_index"] + 1
        next_label = f"➡️ Далее ({done + 1}/{total})" if done < total else "✅ Завершить"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔁 Другой", callback_data=f"redo|{token}"),
            InlineKeyboardButton(text=next_label, callback_data=f"next_track|{token}", style="primary"),
        ]])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔁 Другой вариант", callback_data=f"redo|{token}"),
            InlineKeyboardButton(text="✅ Готово", callback_data=f"pickclose|{token}", style="success"),
        ]])

    try:
        await callback.message.edit_text(success_text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        await callback.message.answer(success_text, parse_mode=ParseMode.HTML, reply_markup=kb)
    # pending оставляем — чтобы можно было выбрать «другой вариант»


@router.callback_query(F.data.startswith("fix|"))
async def cb_fix(callback: CallbackQuery):
    """Кнопка «Не тот трек» под аудио в канале — открыть варианты в ЛС."""
    token = callback.data.split("|", 1)[1]
    data = get_redo(token)
    if not data:
        await callback.answer("⌛ Устарело — пришлите запрос заново.", show_alert=True)
        return
    await callback.answer("Открываю варианты в личке…")
    try:
        await _picker_flow(callback.bot, data["user_id"], data["user_id"], data["query"], data["channel_id"])
    except Exception as e:
        log.error("fix_flow_error", error=str(e))


@router.callback_query(F.data.startswith("redo|"))
async def cb_redo(callback: CallbackQuery):
    """Вернуться к результатам и выбрать другой вариант."""
    token = callback.data.split("|", 1)[1]
    data = _pending_picks.get(token)
    if not data:
        await callback.answer("⌛ Устарело — поищите заново.", show_alert=True)
        return
    if callback.from_user.id != data["user_id"]:
        await callback.answer("Это не ваш поиск.", show_alert=True)
        return
    await callback.answer()
    await _render_picker(callback.message, token, data, data.get("page", 0))


@router.callback_query(F.data.startswith("next_track|"))
async def cb_next_track(callback: CallbackQuery):
    """Перейти к следующему треку (режим выбора)."""
    token = callback.data.split("|", 1)[1]
    data = _pending_picks.pop(token, None)
    if not data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    await callback.answer()
    await _continue_choose(callback.message, data["pk_session"], data["pk_index"] + 1)


@router.callback_query(F.data.startswith("pickclose|"))
async def cb_pick_close(callback: CallbackQuery):
    """Закрыть выбор (убрать кнопки)."""
    token = callback.data.split("|", 1)[1]
    _pending_picks.pop(token, None)
    await callback.answer("Готово")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(F.data.startswith("reopen|"))
async def cb_reopen(callback: CallbackQuery):
    """По прямой ссылке — открыть поиск вариантов."""
    token = callback.data.split("|", 1)[1]
    data = _reopen_pending.pop(token, None)
    if not data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    if callback.from_user.id != data["user_id"]:
        await callback.answer("Это не ваш поиск.", show_alert=True)
        return
    await callback.answer()
    await _show_picker(callback.message, data["query"], data["channel_id"], user_id=callback.from_user.id)


@router.callback_query(F.data.startswith("pickfilter|"))
async def cb_pick_filter(callback: CallbackQuery):
    """Переключение фильтра источника."""
    try:
        _, token, filt = callback.data.split("|")
    except ValueError:
        await callback.answer()
        return
    data = _pending_picks.get(token)
    if not data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    if callback.from_user.id != data["user_id"]:
        await callback.answer("Это не ваш поиск.", show_alert=True)
        return

    data["filter"] = filt
    data["page"] = 0
    await callback.answer()
    await _render_picker(callback.message, token, data, 0)


@router.callback_query(F.data.startswith("pickpage|"))
async def cb_pick_page(callback: CallbackQuery):
    """Листание страниц."""
    try:
        _, token, filt, page = callback.data.split("|")
    except ValueError:
        await callback.answer()
        return
    data = _pending_picks.get(token)
    if not data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    if callback.from_user.id != data["user_id"]:
        await callback.answer("Это не ваш поиск.", show_alert=True)
        return

    data["filter"] = filt
    data["page"] = int(page)
    await callback.answer()
    await _render_picker(callback.message, token, data, int(page))


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("pickcancel|"))
async def cb_pick_cancel(callback: CallbackQuery):
    """Отмена."""
    token = callback.data.split("|", 1)[1]
    data = _pending_picks.pop(token, None)
    if data and data.get("pk_session") is not None:
        _pick_sessions.pop(data["pk_session"], None)
    await callback.answer("Отменено")
    try:
        await callback.message.edit_text("❌ Выбор отменён.")
    except Exception:
        pass


# ----------------------------- callbacks: choose-each -----------------------

@router.callback_query(F.data.startswith("pk_auto|"))
async def cb_pk_auto(callback: CallbackQuery):
    """«Авто для остальных» — обработать оставшиеся треки автоматически."""
    token = callback.data.split("|", 1)[1]
    data = _pending_picks.pop(token, None)
    if not data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    session = _pick_sessions.pop(data["pk_session"], None)
    if not session:
        await callback.answer("Сессия завершена.", show_alert=True)
        return

    remaining = session["tracks"][data["pk_index"]:]
    await callback.answer()
    try:
        await callback.message.edit_text(f"🚀 Остальные ({len(remaining)}) — из VK...")
    except Exception:
        pass
    await _start_processing(
        callback.message, raw_text="", playlist_name_override=session["name"],
        tracks_override=remaining, channel_hint=str(session["channel_id"]), mode="vk",
        user_id=callback.from_user.id,
    )


@router.callback_query(F.data.startswith("pk_skip|"))
async def cb_pk_skip(callback: CallbackQuery):
    """Пропустить текущий трек в режиме выбора."""
    token = callback.data.split("|", 1)[1]
    data = _pending_picks.pop(token, None)
    if not data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    await callback.answer("Пропущено")
    try:
        await callback.message.edit_text("⏭ Пропущено.")
    except Exception:
        pass
    await _continue_choose(callback.message, data["pk_session"], data["pk_index"] + 1)


@router.callback_query(F.data.startswith("pk_skip_track|"))
async def cb_pk_skip_track(callback: CallbackQuery):
    """Пропустить трек, для которого ничего не нашлось (по user_id)."""
    user_id = int(callback.data.split("|", 1)[1])
    if callback.from_user.id != user_id:
        await callback.answer("Это не ваш выбор.", show_alert=True)
        return
    await callback.answer("Пропущено")
    try:
        await callback.message.edit_text("⏭ Пропущено.")
    except Exception:
        pass
    session = _pick_sessions.get(user_id)
    next_index = (session["index"] + 1) if session else 0
    await _continue_choose(callback.message, user_id, next_index)
