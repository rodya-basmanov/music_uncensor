"""Хендлеры библиотеки: /history (история + мои артисты) и /favorites."""

import uuid

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from utils.callback import parse_cb, to_int
from utils.escape import esc_html
from utils.logger import get_logger
from utils.pending import PendingStore
from utils.storage import UserStorage

log = get_logger("library")

router = Router()

_storage: UserStorage | None = None
_lib = PendingStore(ttl=30 * 60)


def set_dependencies(storage: UserStorage) -> None:
    global _storage
    _storage = storage


def _db():
    return _storage.db if _storage is not None else None


@router.message(Command("history"))
async def cmd_history(message: Message):
    """/history — последние загрузки и «мои артисты»."""
    if message.chat.type != "private":
        return
    db = _db()
    if db is None:
        await message.answer("⚠️ Хранилище недоступно.")
        return
    uid = message.from_user.id
    items = db.get_history(uid, 10)
    artists = db.get_my_artists(uid, 6)
    if not items and not artists:
        await message.answer("🕓 История пуста — скачайте что-нибудь, и здесь появится.")
        return

    token = uuid.uuid4().hex[:8]
    _lib[token] = {"user_id": uid, "tracks": items, "artists": artists}
    lines = ["🕓 <b>Недавние загрузки</b>"]
    rows = []
    for i, it in enumerate(items):
        label = f"{it['artist']} — {it['title']}".strip(" —")
        lines.append(f"• {esc_html(label)}")
        rows.append([InlineKeyboardButton(
            text=f"🔎 {label[:48]}", callback_data=f"lib_track|{token}|{i}",
        )])
    if artists:
        lines.append("\n🎤 <b>Мои артисты</b>")
        arow = []
        for i, a in enumerate(artists):
            arow.append(InlineKeyboardButton(
                text=f"{a['artist']} ({a['cnt']})", callback_data=f"lib_artist|{token}|{i}",
            ))
            if len(arow) == 2:
                rows.append(arow)
                arow = []
        if arow:
            rows.append(arow)
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML,
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command("favorites", "fav"))
async def cmd_favorites(message: Message):
    """/favorites — избранные альбомы."""
    if message.chat.type != "private":
        return
    db = _db()
    if db is None:
        await message.answer("⚠️ Хранилище недоступно.")
        return
    uid = message.from_user.id
    favs = db.list_favorites(uid)
    if not favs:
        await message.answer(
            "⭐ Избранное пусто.\n\nОткрой альбом и нажми «⭐ В избранное».",
        )
        return
    token = uuid.uuid4().hex[:8]
    _lib[token] = {"user_id": uid, "favs": favs}
    rows = []
    for i, f in enumerate(favs):
        label = f"{f['artist']} — {f['title']}".strip(" —")
        rows.append([
            InlineKeyboardButton(text=f"💿 {label[:44]}", callback_data=f"lib_album|{token}|{i}"),
            InlineKeyboardButton(text="🗑", callback_data=f"lib_favdel|{token}|{i}"),
        ])
    await message.answer(
        "⭐ <b>Избранные альбомы</b>\nВыбери, чтобы открыть:", parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _channel_for(user_id: int):
    """Канал по умолчанию пользователя (для callback, где message.from_user — бот)."""
    if _storage is None:
        return None
    cid = _storage.get_default_channel(user_id)
    return cid if cid and abs(cid) >= 100 else None


def _get(token: str, user_id: int) -> dict | None:
    data = _lib.get(token)
    if not data or data.get("user_id") != user_id:
        return None
    return data


@router.callback_query(F.data.startswith("lib_track|"))
async def cb_lib_track(callback: CallbackQuery):
    args = parse_cb(callback.data, "lib_track|", 2)
    if args is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    token, idx = args
    data = _get(token, callback.from_user.id)
    if not data or "tracks" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    i = to_int(idx)
    if i is None or not (0 <= i < len(data["tracks"])):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    it = data["tracks"][i]
    channel_id = _channel_for(callback.from_user.id)
    if channel_id is None:
        await callback.answer("Канал не привязан (/link).", show_alert=True)
        return
    await callback.answer("🔎 Ищу…")
    from bot.handlers import playlist
    query = f"{it['artist']} {it['title']}".strip()
    await playlist._show_picker(callback.message, query, channel_id, user_id=callback.from_user.id)


@router.callback_query(F.data.startswith("lib_artist|"))
async def cb_lib_artist(callback: CallbackQuery):
    args = parse_cb(callback.data, "lib_artist|", 2)
    if args is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    token, idx = args
    data = _get(token, callback.from_user.id)
    if not data or "artists" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    i = to_int(idx)
    if i is None or not (0 <= i < len(data["artists"])):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    channel_id = _channel_for(callback.from_user.id)
    if channel_id is None:
        await callback.answer("Канал не привязан (/link).", show_alert=True)
        return
    await callback.answer()
    from bot.handlers import album as album_handler
    await album_handler.show_artist_albums(
        callback.message, data["artists"][i]["artist"], channel_id, callback.from_user.id,
    )


@router.callback_query(F.data.startswith("lib_album|"))
async def cb_lib_album(callback: CallbackQuery):
    args = parse_cb(callback.data, "lib_album|", 2)
    if args is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    token, idx = args
    data = _get(token, callback.from_user.id)
    if not data or "favs" not in data:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    i = to_int(idx)
    if i is None or not (0 <= i < len(data["favs"])):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    channel_id = _channel_for(callback.from_user.id)
    if channel_id is None:
        await callback.answer("Канал не привязан (/link).", show_alert=True)
        return
    fav = data["favs"][i]
    await callback.answer("💿 Открываю…")
    from bot.handlers import album as album_handler
    label = f"{fav['artist']} — {fav['title']}".strip(" —")
    try:
        ok = await album_handler.open_album(
            callback.message, f"{fav['artist']} {fav['title']}".strip(),
            channel_id, callback.from_user.id,
        )
    except Exception as e:
        log.warning("fav_open_failed", error=str(e))
        ok = False
    if not ok:
        await callback.message.answer(
            f"❌ Не нашёл альбом «{esc_html(label)}».\n"
            f"Попробуй /album {esc_html(fav['artist'])}",
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data.startswith("lib_favdel|"))
async def cb_lib_favdel(callback: CallbackQuery):
    args = parse_cb(callback.data, "lib_favdel|", 2)
    if args is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    token, idx = args
    data = _get(token, callback.from_user.id)
    db = _db()
    if not data or "favs" not in data or db is None:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    i = to_int(idx)
    if i is None or not (0 <= i < len(data["favs"])):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    fav = data["favs"].pop(i)
    db.remove_favorite(callback.from_user.id, fav["artist"], fav["title"])
    await callback.answer("Удалено из избранного")
    if not data["favs"]:
        try:
            await callback.message.edit_text("⭐ Избранное пусто.")
        except Exception:
            pass
        return
    rows = []
    for j, f in enumerate(data["favs"]):
        label = f"{f['artist']} — {f['title']}".strip(" —")
        rows.append([
            InlineKeyboardButton(text=f"💿 {label[:44]}", callback_data=f"lib_album|{token}|{j}"),
            InlineKeyboardButton(text="🗑", callback_data=f"lib_favdel|{token}|{j}"),
        ])
    try:
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        pass
