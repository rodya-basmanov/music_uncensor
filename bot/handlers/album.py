"""Хендлер /album — поиск альбома (Deezer) и скачивание треков."""

import asyncio
import re
import time
import uuid

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.handlers import playlist
from services import album as album_service
from services import rzt as rzt_service
from services.album import AlbumInfo
from utils.callback import parse_cb, to_int
from utils.emoji import TextBuilder, ensure_detected, result_key, tier_key
from utils.escape import esc_html
from utils.helpers import Track, crop_text
from utils.logger import get_logger
from utils.pending import PendingStore
from utils.safe import edit_or_send, safe_edit
from utils.storage import UserStorage
from utils.tasks import spawn
from utils.ui import fmt_duration_mmss

log = get_logger("album_handler")

router = Router()

_storage: UserStorage | None = None
_ytdlp_proxy: str = ""
_vk_fetcher = None
_db = None

_pending = PendingStore(ttl=20 * 60)

# Пользователи, ожидающие ввод названия альбома: user_id -> expiry
_awaiting: dict[int, float] = {}
_AWAIT_TTL = 300

# при таком совпадении показываем альбом сразу, без пикера
_AUTO_PICK_SCORE = 90


def _is_awaiting(message: Message) -> bool:
    uid = message.from_user.id if message.from_user else None
    exp = _awaiting.get(uid) if uid else None
    return bool(exp and time.time() <= exp)


def _fallback_queries(query: str) -> list[str]:
    """Варианты запроса для повторного (fuzzy) поиска, если ничего не нашлось."""
    cleaned = re.sub(r"[^\w\s]+", " ", query, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = cleaned.split()

    candidates: list[str] = []
    if cleaned and cleaned.lower() != query.strip().lower():
        candidates.append(cleaned)
    if len(words) > 1:
        candidates.append(" ".join(words[1:]))   # без первого слова (часто артист)
        candidates.append(" ".join(words[:-1]))  # без последнего
    for w in sorted(words, key=len, reverse=True):
        if len(w) > 3:
            candidates.append(w)

    base = query.strip().lower()
    seen: set[str] = set()
    out: list[str] = []
    for s in candidates:
        s = s.strip()
        k = s.lower()
        if s and k != base and k not in seen:
            seen.add(k)
            out.append(s)
    return out


def set_dependencies(storage: UserStorage, ytdlp_proxy: str = "", vk_token: str = "") -> None:
    """Прокидывает зависимости из main."""
    global _storage, _ytdlp_proxy, _vk_fetcher, _db
    _storage = storage
    _ytdlp_proxy = ytdlp_proxy
    _db = getattr(storage, "db", None)
    if vk_token:
        from core.vk_fetcher import VKFetcher
        _vk_fetcher = VKFetcher(vk_token)
    else:
        _vk_fetcher = None


def _cleanup() -> None:
    _pending.sweep()


def _get(token: str, user_id: int) -> dict | None:
    data = _pending.get(token)
    if not data or data["user_id"] != user_id:
        return None
    return data


# ----------------------------- отрисовка -------------------------------------

_RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _fmt_release(date_str: str) -> str:
    """ISO-дату ('2026-07-24' или с временем) → '24 июля 2026'."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str or "")
    if not m:
        return (date_str or "")[:4]
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if 1 <= month <= 12:
        return f"{day} {_RU_MONTHS[month - 1]} {year}"
    return str(year)


def _album_caption(album: AlbumInfo, show_selection: bool = False, selected: set | None = None,
                   rzt: dict | None = None) -> tuple[str, list]:
    """Карточка альбома: (текст, entities). Заголовок bold, треки в blockquote, эмодзи РЗТ/тира — кастомные."""
    b = TextBuilder()
    b.add("💿 ")
    b.add(f"{album.artist} — {album.title}", bold=True)

    meta = []
    if album.artist:
        meta.append(f"🎤 {album.artist}")
    date_label = _fmt_release(album.release_date) if album.release_date else album.year
    if date_label:
        meta.append(f"📅 {date_label}")
    if album.genre:
        meta.append(f"🎧 {album.genre}")
    if meta:
        b.add("\n" + " · ".join(meta))

    info = []
    if album.label:
        info.append(f"🏷 {album.label}")
    if album.tracks:
        info.append(f"{len(album.tracks)} треков")
    if album.total_duration:
        info.append(fmt_duration_mmss(album.total_duration))
    if album.contributors:
        info.append(", ".join(album.contributors[:2]))
    line3 = " · ".join(x for x in info if x)
    if line3:
        b.add("\n" + line3)

    if rzt:
        b.add("\n")
        if rzt.get("rzt") is not None:
            b.add("", emoji_key="rzt")
            b.add(f" РЗТ: {rzt['rzt']}/90")
        if rzt.get("tier"):
            name, val = rzt["tier"]
            b.add(" · ")
            tk = tier_key(name)
            if tk:
                b.add("", emoji_key=tk)
            else:
                b.add("💎")
            b.add(f" {name} {val}")

    # номинации — сворачиваемый блок (чтобы не перегружать карточку)
    if rzt and rzt.get("nominations"):
        b.add("\n")
        b.open_blockquote(expandable=True)
        b.add("🏆 Номинации релиза", bold=True)
        for nom in rzt["nominations"][:6]:
            b.add("\n")
            b.add("", emoji_key=result_key(nom.get("result", ""), nom.get("winner", False)))
            line = f" {nom.get('title', '')}"
            if nom.get("result"):
                line += f" — {nom['result']}"
            if nom.get("period"):
                line += f" · {nom['period']}"
            b.add(line)
        b.close_blockquote()

    n = len(album.tracks)
    if n:
        # держим подпись под лимитом Telegram (1024) и по ширине строки (моб. экран)
        header_len = b.length + 20
        max_rows = max(1, (1000 - header_len) // 30)
        shown = min(n, 18, max_rows)
        rows = []
        for i in range(shown):
            t = album.tracks[i]
            mark = ""
            if show_selection and selected is not None:
                mark = "✅ " if i in selected else "⬜ "
            dur = fmt_duration_mmss(t.duration) if t.duration else "--:--"
            prefix = f"{mark}{i + 1}. "
            suffix = f" · {dur}"
            feat = f" (feat. {', '.join(t.feat)})" if t.feat else ""
            title = crop_text(t.title, max(6, 32 - len(prefix) - len(suffix) - len(feat)))
            rows.append(f"{prefix}{title}{feat}{suffix}")
        if n > shown:
            rows.append(f"… и ещё {n - shown}")
        b.add("\n")
        b.add("\n".join(rows), blockquote=True)
    else:
        b.add("\nТреки недоступны", italic=True)

    if show_selection:
        k = len(selected) if selected is not None else 0
        b.add(f"\nОтмечено: {k} из {n}", italic=True)
    return b.build()


def _mode_label(mode: str) -> str:
    return "VK" if mode == "vk" else "🔀 Uncensored"


def _card_keyboard(token: str, album: AlbumInfo, mode: str = "uncensored",
                   can_alt: bool = False, fav: bool = False) -> InlineKeyboardMarkup:
    n = len(album.tracks)
    rows = [
        [InlineKeyboardButton(
            text=f"⬇️ Скачать весь альбом ({n})",
            callback_data=f"album_dl|{token}", style="primary",
        )],
        [InlineKeyboardButton(text=f"🎚 Источник: {_mode_label(mode)}", callback_data=f"album_mode|{token}")],
        [InlineKeyboardButton(text="☑️ Выбрать треки", callback_data=f"album_sel|{token}")],
        [InlineKeyboardButton(text="🎛 Источник для каждого", callback_data=f"album_src|{token}")],
    ]
    if can_alt:
        rows.append([InlineKeyboardButton(text="🔁 Не тот альбом", callback_data=f"album_alt|{token}")])
    rows.append([InlineKeyboardButton(
        text="★ Убрать из избранного" if fav else "⭐ В избранное",
        callback_data=f"album_fav|{token}",
    )])
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data=f"album_close|{token}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _is_fav(data: dict, album: AlbumInfo) -> bool:
    if _db is None:
        return False
    try:
        return _db.is_favorite(data["user_id"], album.artist, album.title)
    except Exception:
        return False


def _select_keyboard(token: str, album: AlbumInfo, selected: set, mode: str = "uncensored") -> InlineKeyboardMarkup:
    rows = []
    for i, t in enumerate(album.tracks):
        mark = "✅" if i in selected else "⬜"
        label = f"{mark} {i + 1}. {t.title}"
        if len(label) > 60:
            label = label[:59] + "…"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"album_tg|{token}|{i}")])
    rows.append([InlineKeyboardButton(text=f"🎚 Источник: {_mode_label(mode)}", callback_data=f"album_mode|{token}")])
    rows.append([InlineKeyboardButton(
        text=f"⬇️ Скачать выбранные ({len(selected)})",
        callback_data=f"album_dlsel|{token}", style="primary",
    )])
    rows.append([
        InlineKeyboardButton(text="✅ Все", callback_data=f"album_all|{token}"),
        InlineKeyboardButton(text="⬜ Снять", callback_data=f"album_none|{token}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"album_back|{token}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_album_view(message: Message, token: str, data: dict) -> None:
    """Перерисовывает карточку/выбор треков с учётом текущего источника."""
    album = data["album"]
    mode = data.get("mode", "uncensored")
    rzt = data.get("rzt")
    if data.get("view") == "select":
        text, ents = _album_caption(album, show_selection=True, selected=data["selected"], rzt=rzt)
        await _edit_card(
            message, album, text, ents,
            _select_keyboard(token, album, data["selected"], mode),
        )
    else:
        text, ents = _album_caption(album, rzt=rzt)
        can_alt = len(data.get("alternatives") or []) > 1
        await _edit_card(message, album, text, ents,
                         _card_keyboard(token, album, mode, can_alt, _is_fav(data, album)))


async def _send_card(bot, chat_id: int, album: AlbumInfo, caption: str, entities: list, kb: InlineKeyboardMarkup):
    if album.cover_url:
        try:
            return await bot.send_photo(
                chat_id, photo=album.cover_url, caption=caption,
                caption_entities=entities, reply_markup=kb,
            )
        except Exception as e:
            log.warning("album_photo_failed", error=str(e))
    try:
        return await bot.send_message(chat_id, caption, entities=entities, reply_markup=kb)
    except Exception:
        return await bot.send_message(chat_id, caption, reply_markup=kb)


async def _edit_card(message: Message, album: AlbumInfo, caption: str, entities: list,
                     kb: InlineKeyboardMarkup) -> None:
    if not await safe_edit(message, caption, entities=entities, reply_markup=kb):
        try:
            await message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass


async def _enrich_album(bot, sent, token: str, album: AlbumInfo, user_id: int) -> None:
    """Фоново: чинит регистр артиста и тянет РЗТ, затем дорисовывает карточку."""
    try:
        await ensure_detected(bot, sent.chat.id)
    except Exception:
        pass
    casing, rzt, feats_added = await asyncio.gather(
        album_service.pretty_artist(album.artist, _ytdlp_proxy, _vk_fetcher),
        rzt_service.get_rating_for(album.artist, album.title),
        album_service.enrich_with_vk(album, _vk_fetcher),
        return_exceptions=True,
    )
    if isinstance(casing, BaseException):
        casing = ""
    if isinstance(rzt, BaseException):
        rzt = None
    if isinstance(feats_added, BaseException):
        feats_added = 0
    data = _pending.get(token)
    if not data or data.get("user_id") != user_id:
        return
    casing_changed = bool(casing) and casing != album.artist
    if casing_changed:
        album.artist = casing
    if rzt:
        data["rzt"] = rzt
    if not casing_changed and not rzt and not feats_added:
        return
    try:
        if data.get("view") == "select":
            text, ents = _album_caption(album, show_selection=True, selected=data["selected"], rzt=rzt)
            await _edit_card(
                sent, album, text, ents,
                _select_keyboard(token, album, data["selected"], data.get("mode", "uncensored")),
            )
        else:
            text, ents = _album_caption(album, rzt=rzt)
            can_alt = len(data.get("alternatives") or []) > 1
            await _edit_card(
                sent, album, text, ents,
                _card_keyboard(token, album, data.get("mode", "uncensored"), can_alt, _is_fav(data, album)),
            )
    except Exception:
        pass


async def _present_album(message: Message, alb: AlbumInfo, channel_id: int, user_id: int,
                         alternatives: list | None = None) -> None:
    """Догружает альбом, чинит регистр артиста и сразу показывает карточку (РЗТ — фоном)."""
    _awaiting.pop(user_id, None)
    if alb.source == "vk":
        full = await album_service.get_album_vk(alb, _vk_fetcher) or alb
    else:
        full = await album_service.get_album(alb.source, alb.id, _ytdlp_proxy) or alb
    alts = alternatives if alternatives is not None else [alb]

    token = uuid.uuid4().hex[:8]
    _pending[token] = {
        "user_id": user_id,
        "channel_id": channel_id,
        "album": full,
        "alternatives": alts,
        "selected": set(range(len(full.tracks))),
        "mode": "uncensored",
        "view": "card",
        "rzt": None,
        "created": time.time(),
    }
    text, ents = _album_caption(full)
    kb = _card_keyboard(token, full, "uncensored", len(alts) > 1, _is_fav(_pending[token], full))
    try:
        await message.delete()
    except Exception:
        pass
    sent = await _send_card(message.bot, message.chat.id, full, text, ents, kb)
    if sent is not None:
        spawn(_enrich_album(message.bot, sent, token, full, user_id), name="enrich_album")


# ----------------------------- команда ---------------------------------------

@router.callback_query(F.data == "album_ask")
async def cb_album_ask(callback: CallbackQuery):
    """Кнопка «💿 Альбом» — ждём название альбома следующим сообщением."""
    await callback.answer()
    uid = callback.from_user.id
    _awaiting[uid] = time.time() + _AWAIT_TTL
    await callback.message.answer(
        "💿 <b>Скачать альбом</b>\n\n"
        "Напиши название — артист и альбом, например:\n"
        "<code>VILLIAN Красивое Зло</code>",
        parse_mode=ParseMode.HTML,
    )


@router.message(F.chat.type == "private", F.text & ~F.text.startswith("/"), _is_awaiting)
async def handle_album_text(message: Message):
    """Следующее текстовое сообщение после кнопки «Альбом» — название альбома."""
    uid = message.from_user.id
    _awaiting.pop(uid, None)
    text = (message.text or "").strip()
    if "http" in text.lower() or not text:
        await message.answer("Ок, отменил ввод альбома. Пришли название текстом.")
        return
    await _start_album_search(message, text)


_PICKER_PAGE = 8


def _picker_view(token: str, data: dict, page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и клавиатура пикера альбомов с пагинацией."""
    albums = data["albums"]
    total_pages = max(1, (len(albums) + _PICKER_PAGE - 1) // _PICKER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * _PICKER_PAGE
    chunk = albums[start:start + _PICKER_PAGE]
    rows = []
    for j, a in enumerate(chunk):
        i = start + j
        label = f"{a.artist} — {a.title}"
        if a.year:
            label += f" ({a.year})"
        if len(label) > 60:
            label = label[:59] + "…"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"albumpick|{token}|{i}")])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"albumpage|{token}|{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"· {page + 1}/{total_pages} ·", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"albumpage|{token}|{page + 1}"))
        rows.append(nav)
    if data.get("track_query"):
        rows.append([InlineKeyboardButton(
            text=f"🔎 Искать как трек: {data['track_query'][:24]}",
            callback_data=f"tracksearch|{token}",
        )])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="album_reenter", style="danger")])
    if data.get("header"):
        text = data["header"]
    elif data.get("fuzzy"):
        text = "💿 <b>Возможно, ты имел в виду — выбери альбом 👇</b>"
    else:
        text = "💿 <b>Нашёл альбомы:</b>\nВыбери нужный 👇"
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_picker_msg(message: Message, token: str, data: dict, page: int) -> None:
    text, kb = _picker_view(token, data, page)
    try:
        if getattr(message, "photo", None):
            await message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _show_album_picker(message: Message, albums: list, channel_id: int, user_id: int,
                             fuzzy: bool = False, header: str | None = None,
                             track_query: str | None = None, set_awaiting: bool = True) -> None:
    if not albums:
        return
    # пока список открыт — следующее сообщение снова ищем как альбом
    if set_awaiting:
        _awaiting[user_id] = time.time() + _AWAIT_TTL
    token = uuid.uuid4().hex[:8]
    _pending[token] = {
        "user_id": user_id, "channel_id": channel_id,
        "albums": albums, "created": time.time(), "page": 0,
        "fuzzy": fuzzy, "header": header, "track_query": track_query,
    }
    await _render_picker_msg(message, token, _pending[token], 0)


async def show_artist_albums(message: Message, query: str, channel_id: int, user_id: int,
                             track_fallback: str | None = None) -> bool:
    """Для обычного трек-поиска: если запрос — имя артиста, показать его альбомы.

    Возвращает True, если альбомы найдены и пикер показан.
    """
    try:
        artist = await album_service.search_artist(query, _ytdlp_proxy)
        albums = await album_service.search_artist_albums(query, _ytdlp_proxy) if artist else []
    except Exception:
        return False
    if not artist or not albums:
        return False
    header = f"💿 <b>{esc_html(artist['name'])}</b> — выбери альбом 👇"
    status = await message.answer("💿 Ищу альбомы артиста…", parse_mode=ParseMode.HTML)
    await _show_album_picker(
        status, albums, channel_id, user_id,
        header=header, track_query=track_fallback, set_awaiting=False,
    )
    return True


async def open_album(message: Message, query: str, channel_id: int, user_id: int) -> bool:
    """Открывает альбом по строке «Артист Название» (для /history и /favorites)."""
    try:
        albums = await album_service.search_albums(query, _ytdlp_proxy)
    except Exception:
        albums = []
    if not albums:
        return False
    if len(albums) == 1 or album_service._album_score(query, albums[0]) >= _AUTO_PICK_SCORE:
        await _present_album(message, albums[0], channel_id, user_id, albums)
    else:
        await _show_album_picker(message, albums, channel_id, user_id)
    return True


async def _start_album_search(message: Message, query: str) -> None:
    """Общая логика поиска альбома (для /album и ввода после кнопки)."""
    user_id = message.from_user.id
    # держим «режим альбома»: следующее сообщение снова ищет альбом
    _awaiting[user_id] = time.time() + _AWAIT_TTL

    if playlist.is_user_busy(user_id):
        await message.answer("⏳ У вас уже выполняется задача. Дождитесь завершения или /cancel.")
        return

    channel_id = playlist._resolve_target_channel(message, None)
    if channel_id is None:
        await message.answer(
            "❌ <b>Канал не определён</b>\n\nПривяжите канал: <code>/link -100XXXXXXXXXX</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    status = await message.answer(
        f"💿 Ищу альбом: <i>{esc_html(query)}</i>...", parse_mode=ParseMode.HTML,
    )

    # Режим артиста: «saluki» → все альбомы артиста (без авто-выбора).
    try:
        artist = await album_service.search_artist(query, _ytdlp_proxy)
    except Exception:
        artist = None
    if artist:
        try:
            artist_albums = await album_service.search_artist_albums(query, _ytdlp_proxy)
        except Exception:
            artist_albums = []
        if artist_albums:
            _cleanup()
            await _show_album_picker(
                status, artist_albums, channel_id, user_id,
                header=f"💿 <b>{esc_html(artist['name'])}</b> — выбери альбом 👇",
            )
            return

    try:
        albums = await album_service.search_albums(query, _ytdlp_proxy)
    except Exception as e:
        log.error("album_search_error", error=str(e))
        albums = []

    if not albums:
        # Deezer не дал результат/ошибка — пробуем VK
        try:
            await status.edit_text(f"💿 Deezer не ответил, ищу в VK: <i>{esc_html(query)}</i>...", parse_mode=ParseMode.HTML)
        except Exception:
            pass
        try:
            albums = await album_service.search_albums_vk(query, _vk_fetcher)
        except Exception as e:
            log.error("album_search_vk_error", error=str(e))
            albums = []

    _cleanup()
    if not albums:
        await status.edit_text(
            f"❌ Альбом не найден «{esc_html(query)}».{playlist._no_results_hint()}",
            parse_mode=ParseMode.HTML,
        )
        return
    # уверенное совпадение — показываем сразу, иначе пикер
    if len(albums) == 1 or album_service._album_score(query, albums[0]) >= _AUTO_PICK_SCORE:
        await _present_album(status, albums[0], channel_id, user_id, albums)
        return
    await _show_album_picker(status, albums, channel_id, user_id)


@router.message(Command("album"))
async def cmd_album(message: Message):
    """/album <Артист Название> — найти альбом и скачать треки."""
    if message.chat.type != "private":
        return
    parts = (message.text or "").split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""
    if not query:
        _awaiting[message.from_user.id] = time.time() + _AWAIT_TTL
        await message.answer(
            "💿 <b>Скачать альбом</b>\n\n"
            "Напиши название — артист и альбом, например:\n"
            "<code>VILLIAN Красивое Зло</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    await _start_album_search(message, query)


# ----------------------------- callbacks -------------------------------------

def _cb_token(callback: CallbackQuery, prefix: str):
    """Безопасно достаёт token из callback_data и данные сессии пользователя."""
    args = parse_cb(callback.data, prefix, 1)
    if args is None:
        return None, None
    return args[0], _get(args[0], callback.from_user.id)


@router.callback_query(F.data.startswith("albumpick|"))
async def cb_album_pick(callback: CallbackQuery):
    args = parse_cb(callback.data, "albumpick|", 2)
    if args is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    token, idx = args
    data = _get(token, callback.from_user.id)
    if not data or "albums" not in data:
        await callback.answer("⌛ Устарело — поищите заново.", show_alert=True)
        return
    i = to_int(idx)
    if i is None or not (0 <= i < len(data["albums"])):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    await callback.answer()
    await _present_album(
        callback.message, data["albums"][i], data["channel_id"], data["user_id"],
        alternatives=data["albums"],
    )


@router.callback_query(F.data.startswith("albumpage|"))
async def cb_album_page(callback: CallbackQuery):
    """Листание страниц списка альбомов артиста."""
    args = parse_cb(callback.data, "albumpage|", 2)
    if args is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    token, page = args
    data = _get(token, callback.from_user.id)
    if not data or "albums" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    p = to_int(page)
    if p is None:
        await callback.answer()
        return
    data["page"] = p
    await callback.answer()
    await _render_picker_msg(callback.message, token, data, p)


@router.callback_query(F.data.startswith("tracksearch|"))
async def cb_track_search(callback: CallbackQuery):
    """Из пикера артиста — искать всё-таки трек."""
    token, data = _cb_token(callback, "tracksearch|")
    if token is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    q = data.get("track_query") if data else None
    if not q:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    await callback.answer("🔎 Ищу трек…")
    await playlist._show_picker(callback.message, q, data["channel_id"], user_id=callback.from_user.id)


@router.callback_query(F.data.startswith("album_fav|"))
async def cb_album_fav(callback: CallbackQuery):
    """Добавить/убрать альбом из избранного."""
    token, data = _cb_token(callback, "album_fav|")
    if token is None or not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    album = data["album"]
    uid = data["user_id"]
    if _db is None or callback.from_user.id != uid:
        await callback.answer("Недоступно.", show_alert=True)
        return
    if _db.is_favorite(uid, album.artist, album.title):
        _db.remove_favorite(uid, album.artist, album.title)
        await callback.answer("Убрано из избранного")
    else:
        _db.add_favorite(uid, album.artist, album.title)
        await callback.answer("⭐ Добавлено в избранное")
    await _render_album_view(callback.message, token, data)


@router.callback_query(F.data.startswith("album_sel|"))
async def cb_album_sel(callback: CallbackQuery):
    token, data = _cb_token(callback, "album_sel|")
    if token is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    data["view"] = "select"
    await callback.answer()
    await _render_album_view(callback.message, token, data)


@router.callback_query(F.data.startswith("album_tg|"))
async def cb_album_toggle(callback: CallbackQuery):
    args = parse_cb(callback.data, "album_tg|", 2)
    if args is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    token, idx = args
    data = _get(token, callback.from_user.id)
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    i = to_int(idx)
    if i is None or not (0 <= i < len(data["album"].tracks)):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    selected = data["selected"]
    if i in selected:
        selected.discard(i)
    else:
        selected.add(i)
    data["view"] = "select"
    await callback.answer()
    await _render_album_view(callback.message, token, data)


@router.callback_query(F.data.startswith("album_all|"))
async def cb_album_all(callback: CallbackQuery):
    token, data = _cb_token(callback, "album_all|")
    if token is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    data["selected"] = set(range(len(data["album"].tracks)))
    data["view"] = "select"
    await callback.answer()
    await _render_album_view(callback.message, token, data)


@router.callback_query(F.data.startswith("album_none|"))
async def cb_album_none(callback: CallbackQuery):
    token, data = _cb_token(callback, "album_none|")
    if token is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    data["selected"] = set()
    data["view"] = "select"
    await callback.answer()
    await _render_album_view(callback.message, token, data)


@router.callback_query(F.data.startswith("album_mode|"))
async def cb_album_mode(callback: CallbackQuery):
    """Переключает источник скачивания: uncensored ⇄ VK."""
    token, data = _cb_token(callback, "album_mode|")
    if token is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    data["mode"] = "vk" if data.get("mode") != "vk" else "uncensored"
    await callback.answer(f"Источник: {_mode_label(data['mode'])}")
    await _render_album_view(callback.message, token, data)


@router.callback_query(F.data.startswith("album_back|"))
async def cb_album_back(callback: CallbackQuery):
    token, data = _cb_token(callback, "album_back|")
    if token is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    data["view"] = "card"
    await callback.answer()
    await _render_album_view(callback.message, token, data)


@router.callback_query(F.data.startswith("album_alt|"))
async def cb_album_alt(callback: CallbackQuery):
    """«Не тот альбом» — вернуться к списку найденных вариантов."""
    token, data = _cb_token(callback, "album_alt|")
    if token is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    alts = data.get("alternatives") or []
    if len(alts) < 2:
        await callback.answer("Других вариантов нет.", show_alert=True)
        return
    await callback.answer()
    await _show_album_picker(callback.message, alts, data["channel_id"], data["user_id"])


@router.callback_query(F.data == "album_reenter")
async def cb_album_reenter(callback: CallbackQuery):
    """«Отмена» в списке альбомов — не удаляем, а снова просим ввести название."""
    await callback.answer()
    _awaiting[callback.from_user.id] = time.time() + _AWAIT_TTL
    text = (
        "💿 <b>Скачать альбом</b>\n\n"
        "Напиши название — артист и альбом, например:\n"
        "<code>VILLIAN Красивое Зло</code>"
    )
    await edit_or_send(callback.message, text, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("album_close|"))
async def cb_album_close(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass


async def _launch(
    callback: CallbackQuery,
    data: dict,
    tracks: list[Track],
    name: str,
    channel_header: bool = True,
) -> None:
    if not tracks:
        await callback.answer("Ничего не выбрано.", show_alert=True)
        return
    mode = data.get("mode", "uncensored")
    await callback.answer("🚀 Запускаю…")
    # Карточку альбома НЕ удаляем — прогресс пойдёт отдельным сообщением в ЛС.
    await playlist._start_processing(
        callback.message, "",
        channel_hint=str(data["channel_id"]),
        playlist_name_override=name,
        tracks_override=tracks,
        mode=mode,
        user_id=data["user_id"],
        cover_url=data["album"].cover_url,
        channel_header=channel_header,
    )


def _album_tracks(album: AlbumInfo, indices=None) -> list[Track]:
    """Треки альбома с обложкой, фитами, VK-ссылкой (если есть) и форсированными метаданными."""
    out = []
    for i, t in enumerate(album.tracks):
        if indices is not None and i not in indices:
            continue
        title = f"{t.title} (feat. {', '.join(t.feat)})" if t.feat else t.title
        out.append(Track(
            artist=album.artist, title=title, duration=t.duration,
            url=t.url, source=t.source,
            cover_url=t.cover_url or album.cover_url, force_meta=True,
        ))
    return out


@router.callback_query(F.data.startswith("album_dl|"))
async def cb_album_dl(callback: CallbackQuery):
    token, data = _cb_token(callback, "album_dl|")
    if token is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    album = data["album"]
    await _launch(callback, data, _album_tracks(album), f"{album.artist} — {album.title}")


@router.callback_query(F.data.startswith("album_dlsel|"))
async def cb_album_dlsel(callback: CallbackQuery):
    token, data = _cb_token(callback, "album_dlsel|")
    if token is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    album = data["album"]
    selected = data["selected"]
    # Частичная загрузка — шапку/обложку альбома в канал НЕ отправляем.
    await _launch(
        callback, data, _album_tracks(album, selected),
        f"{album.artist} — {album.title}", channel_header=False,
    )


@router.callback_query(F.data.startswith("album_src|"))
async def cb_album_src(callback: CallbackQuery):
    """«Источник для каждого» — пикер источника/версии по каждому треку альбома."""
    token, data = _cb_token(callback, "album_src|")
    if token is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    if playlist.is_user_busy(callback.from_user.id):
        await callback.answer("⏳ У вас уже выполняется задача. Дождитесь или /cancel.", show_alert=True)
        return
    album = data["album"]
    await callback.answer("🎛 Выбираем источник по трекам…")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await playlist.start_choose_session(
        callback.message, callback.from_user.id, data["channel_id"],
        f"{album.artist} — {album.title}", _album_tracks(album),
    )
