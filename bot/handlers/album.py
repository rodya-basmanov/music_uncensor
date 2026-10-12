"""Хендлер /album — поиск альбома (Deezer/iTunes) и скачивание треков."""

import re
import time
import uuid

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.handlers import playlist
from services import album as album_service
from services.album import AlbumInfo
from utils.escape import esc_html
from utils.helpers import Track
from utils.logger import get_logger
from utils.storage import UserStorage
from utils.ui import fmt_duration_mmss

log = get_logger("album_handler")

router = Router()

_storage: UserStorage | None = None
_ytdlp_proxy: str = ""
_vk_fetcher = None

_PENDING_TTL = 20 * 60
_pending: dict[str, dict] = {}

# Пользователи, ожидающие ввод названия альбома: user_id -> expiry
_awaiting: dict[int, float] = {}
_AWAIT_TTL = 300


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
    global _storage, _ytdlp_proxy, _vk_fetcher
    _storage = storage
    _ytdlp_proxy = ytdlp_proxy
    if vk_token:
        from core.vk_fetcher import VKFetcher
        _vk_fetcher = VKFetcher(vk_token)
    else:
        _vk_fetcher = None


def _cleanup() -> None:
    now = time.time()
    for tok in [t for t, v in _pending.items() if now - v["created"] > _PENDING_TTL]:
        _pending.pop(tok, None)


def _get(token: str, user_id: int) -> dict | None:
    data = _pending.get(token)
    if not data or data["user_id"] != user_id:
        return None
    return data


# ----------------------------- отрисовка -------------------------------------

def _album_caption(album: AlbumInfo, show_selection: bool = False, selected: set | None = None) -> str:
    """Карточка альбома: заголовок + мета + таблица треков в <code>."""
    head = f"💿 <b>{esc_html(album.artist)} — {esc_html(album.title)}</b>"
    meta = []
    if album.artist:
        meta.append(f"🎤 {esc_html(album.artist)}")
    if album.year:
        meta.append(f"📅 {album.year}")
    if album.genre:
        meta.append(f"🎧 {esc_html(album.genre)}")
    line2 = " · ".join(meta)

    info = []
    if album.label:
        info.append(f"🏷 {esc_html(album.label)}")
    if album.tracks:
        info.append(f"{len(album.tracks)} треков")
    if album.total_duration:
        info.append(fmt_duration_mmss(album.total_duration))
    if album.contributors:
        info.append(esc_html(", ".join(album.contributors[:2])))
    line3 = " · ".join(x for x in info if x)

    lines = [head]
    if line2:
        lines.append(line2)
    if line3:
        lines.append(line3)

    n = len(album.tracks)
    if n:
        shown = min(n, 18)
        rows = []
        for i in range(shown):
            t = album.tracks[i]
            mark = ""
            if show_selection and selected is not None:
                mark = "✅ " if i in selected else "⬜ "
            title = t.title
            if len(title) > 28:
                title = title[:27] + "…"
            dur = fmt_duration_mmss(t.duration) if t.duration else "--:--"
            rows.append(f"{mark}{i + 1:>2}. {title:<28} {dur}")
        if n > shown:
            rows.append(f"     … и ещё {n - shown}")
        lines.append("<code>" + esc_html("\n".join(rows)) + "</code>")
    else:
        lines.append("<i>Треки недоступны</i>")

    if show_selection:
        k = len(selected) if selected is not None else 0
        lines.append(f"<i>Отмечено: {k} из {n}</i>")
    return "\n".join(lines)


def _card_keyboard(token: str, album: AlbumInfo) -> InlineKeyboardMarkup:
    n = len(album.tracks)
    rows = [
        [InlineKeyboardButton(
            text=f"⬇️ Скачать весь альбом ({n})",
            callback_data=f"album_dl|{token}", style="primary",
        )],
        [InlineKeyboardButton(text="☑️ Выбрать треки", callback_data=f"album_sel|{token}")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data=f"album_close|{token}", style="danger")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _select_keyboard(token: str, album: AlbumInfo, selected: set) -> InlineKeyboardMarkup:
    rows = []
    for i, t in enumerate(album.tracks):
        mark = "✅" if i in selected else "⬜"
        label = f"{mark} {i + 1}. {t.title}"
        if len(label) > 60:
            label = label[:59] + "…"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"album_tg|{token}|{i}")])
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


async def _send_card(bot, chat_id: int, album: AlbumInfo, caption: str, kb: InlineKeyboardMarkup):
    if album.cover_url:
        try:
            return await bot.send_photo(
                chat_id, photo=album.cover_url, caption=caption,
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        except Exception as e:
            log.warning("album_photo_failed", error=str(e))
    return await bot.send_message(chat_id, caption, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _edit_card(message: Message, album: AlbumInfo, caption: str, kb: InlineKeyboardMarkup) -> None:
    if message.photo:
        try:
            await message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except Exception:
            pass
    try:
        await message.edit_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    except Exception:
        pass
    try:
        await message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass


async def _present_album(message: Message, alb: AlbumInfo, channel_id: int, user_id: int) -> None:
    """Догружает альбом, чинит регистр артиста и показывает карточку."""
    full = await album_service.get_album(alb.source, alb.id, _ytdlp_proxy) or alb
    full.artist = await album_service.pretty_artist(full.artist, _ytdlp_proxy, _vk_fetcher)

    token = uuid.uuid4().hex[:8]
    _pending[token] = {
        "user_id": user_id,
        "channel_id": channel_id,
        "album": full,
        "selected": set(range(len(full.tracks))),
        "created": time.time(),
    }
    caption = _album_caption(full)
    kb = _card_keyboard(token, full)
    try:
        await message.delete()
    except Exception:
        pass
    await _send_card(message.bot, message.chat.id, full, caption, kb)


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


async def _show_album_picker(message: Message, albums: list, channel_id: int, user_id: int, fuzzy: bool = False) -> None:
    token = uuid.uuid4().hex[:8]
    _pending[token] = {
        "user_id": user_id, "channel_id": channel_id,
        "albums": albums, "created": time.time(),
    }
    rows = []
    for i, a in enumerate(albums):
        label = f"{a.artist} — {a.title}"
        if a.year:
            label += f" ({a.year})"
        if len(label) > 60:
            label = label[:59] + "…"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"albumpick|{token}|{i}")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"album_close|{token}", style="danger")])
    if fuzzy:
        header = "💿 <b>Возможно, ты имел в виду — выбери альбом 👇</b>"
    else:
        header = "💿 <b>Нашёл альбомы:</b>\nВыбери нужный 👇"
    await message.edit_text(header, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _start_album_search(message: Message, query: str) -> None:
    """Общая логика поиска альбома (для /album и ввода после кнопки)."""
    user_id = message.from_user.id

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
    try:
        albums = await album_service.search_albums(query, _ytdlp_proxy)
    except Exception as e:
        log.error("album_search_error", error=str(e))
        albums = []

    if not albums:
        pool: list = []
        seen_ids: set = set()
        for alt in _fallback_queries(query)[:4]:
            try:
                found = await album_service.search_albums(alt, _ytdlp_proxy)
            except Exception:
                found = []
            for a in found:
                key = (a.source, a.id)
                if key not in seen_ids:
                    seen_ids.add(key)
                    pool.append(a)
            if len(pool) >= 6:
                break
        if pool:
            _cleanup()
            if len(pool) == 1:
                await _present_album(status, pool[0], channel_id, user_id)
            else:
                await _show_album_picker(status, pool, channel_id, user_id, fuzzy=True)
            return
        await status.edit_text(
            f"❌ Альбом не найден.{playlist._no_results_hint()}", parse_mode=ParseMode.HTML,
        )
        return

    _cleanup()
    if len(albums) == 1:
        await _present_album(status, albums[0], channel_id, user_id)
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

@router.callback_query(F.data.startswith("albumpick|"))
async def cb_album_pick(callback: CallbackQuery):
    try:
        _, token, idx = callback.data.split("|")
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    data = _get(token, callback.from_user.id)
    if not data or "albums" not in data:
        await callback.answer("⌛ Устарело — поищите заново.", show_alert=True)
        return
    await callback.answer()
    await _present_album(callback.message, data["albums"][int(idx)], data["channel_id"], data["user_id"])


@router.callback_query(F.data.startswith("album_sel|"))
async def cb_album_sel(callback: CallbackQuery):
    token = callback.data.split("|", 1)[1]
    data = _get(token, callback.from_user.id)
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    album, selected = data["album"], data["selected"]
    await callback.answer()
    await _edit_card(
        callback.message, album,
        _album_caption(album, show_selection=True, selected=selected),
        _select_keyboard(token, album, selected),
    )


@router.callback_query(F.data.startswith("album_tg|"))
async def cb_album_toggle(callback: CallbackQuery):
    try:
        _, token, idx = callback.data.split("|")
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    data = _get(token, callback.from_user.id)
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    i = int(idx)
    selected = data["selected"]
    if i in selected:
        selected.discard(i)
    else:
        selected.add(i)
    album = data["album"]
    await callback.answer()
    await _edit_card(
        callback.message, album,
        _album_caption(album, show_selection=True, selected=selected),
        _select_keyboard(token, album, selected),
    )


@router.callback_query(F.data.startswith("album_all|"))
async def cb_album_all(callback: CallbackQuery):
    token = callback.data.split("|", 1)[1]
    data = _get(token, callback.from_user.id)
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    album = data["album"]
    data["selected"] = set(range(len(album.tracks)))
    await callback.answer()
    await _edit_card(
        callback.message, album,
        _album_caption(album, show_selection=True, selected=data["selected"]),
        _select_keyboard(token, album, data["selected"]),
    )


@router.callback_query(F.data.startswith("album_none|"))
async def cb_album_none(callback: CallbackQuery):
    token = callback.data.split("|", 1)[1]
    data = _get(token, callback.from_user.id)
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    album = data["album"]
    data["selected"] = set()
    await callback.answer()
    await _edit_card(
        callback.message, album,
        _album_caption(album, show_selection=True, selected=data["selected"]),
        _select_keyboard(token, album, data["selected"]),
    )


@router.callback_query(F.data.startswith("album_back|"))
async def cb_album_back(callback: CallbackQuery):
    token = callback.data.split("|", 1)[1]
    data = _get(token, callback.from_user.id)
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    album = data["album"]
    await callback.answer()
    await _edit_card(callback.message, album, _album_caption(album), _card_keyboard(token, album))


@router.callback_query(F.data.startswith("album_close|"))
async def cb_album_close(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass


async def _launch(callback: CallbackQuery, data: dict, tracks: list[Track], name: str) -> None:
    if not tracks:
        await callback.answer("Ничего не выбрано.", show_alert=True)
        return
    await callback.answer("🚀 Запускаю…")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await playlist._start_processing(
        callback.message, "",
        channel_hint=str(data["channel_id"]),
        playlist_name_override=name,
        tracks_override=tracks,
        mode="uncensored",
        user_id=data["user_id"],
    )


def _album_tracks(album: AlbumInfo, indices=None) -> list[Track]:
    """Треки альбома с обложкой альбома и форсированными метаданными."""
    out = []
    for i, t in enumerate(album.tracks):
        if indices is not None and i not in indices:
            continue
        out.append(Track(
            artist=album.artist, title=t.title, duration=t.duration,
            cover_url=album.cover_url, force_meta=True,
        ))
    return out


@router.callback_query(F.data.startswith("album_dl|"))
async def cb_album_dl(callback: CallbackQuery):
    token = callback.data.split("|", 1)[1]
    data = _get(token, callback.from_user.id)
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    album = data["album"]
    await _launch(callback, data, _album_tracks(album), f"{album.artist} — {album.title}")


@router.callback_query(F.data.startswith("album_dlsel|"))
async def cb_album_dlsel(callback: CallbackQuery):
    token = callback.data.split("|", 1)[1]
    data = _get(token, callback.from_user.id)
    if not data or "album" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    album = data["album"]
    selected = data["selected"]
    await _launch(callback, data, _album_tracks(album, selected), f"{album.artist} — {album.title}")
