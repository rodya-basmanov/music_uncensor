"""Хендлеры привязки каналов: /link, /channels, /unlink."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode

from utils.storage import UserStorage
from utils.logger import get_logger

log = get_logger("link_handler")

router = Router()

# Хранилище инжектится через middleware/глобальную переменную
_storage: UserStorage | None = None


def set_storage(storage: UserStorage) -> None:
    """Устанавливает хранилище (вызывается при инициализации бота)."""
    global _storage
    _storage = storage


@router.message(Command("link"))
async def cmd_link(message: Message):
    """
    /link — привязывает канал.

    В ЛС: /link -100XXXXXXXXXX — привязывает канал по ID
    В канале: /link — привязывает текущий канал
    """
    chat_type = message.chat.type
    user_id = message.from_user.id if message.from_user else None

    # В ЛС — ожидаем chat_id как аргумент
    if chat_type == "private":
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "📋 Использование:\n"
                "<code>/link -100XXXXXXXXXX</code> — привязать канал\n\n"
                "Или отправьте /link <b>в канале</b>, где бот — администратор.",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            channel_id = int(args[1].strip())
        except ValueError:
            await message.answer("❌ Неверный формат chat_id. Должно быть число.")
            return
        if abs(channel_id) < 100:
            await message.answer("❌ Неверный chat_id.")
            return
        if _storage and user_id:
            _storage.add_channel(user_id, channel_id)
        await message.answer(
            f"✅ Канал <code>{channel_id}</code> привязан и установлен по умолчанию.\n\n"
            f"Теперь просто отправляйте треки — они пойдут в этот канал.\n"
            f"Сменить канал: <code>/link -100XXXXXXXXXX</code>",
            parse_mode=ParseMode.HTML,
        )
        log.info("link_command_dm", chat_id=channel_id, user_id=user_id)
        return

    # В канале/группе — сохраняем привязку
    chat_id = message.chat.id
    if _storage and user_id:
        _storage.add_channel(user_id, chat_id)

    await message.answer(
        f"✅ Chat ID этого канала:\n"
        f"<code>{chat_id}</code>\n\n"
        f"Канал привязан и установлен по умолчанию.",
        parse_mode=ParseMode.HTML,
    )
    log.info("link_command_channel", chat_id=chat_id, user_id=user_id)


@router.message(Command("channels"))
async def cmd_channels(message: Message):
    """/channels — показывает список привязанных каналов."""
    if message.chat.type != "private":
        return

    if not _storage:
        await message.answer("⚠️ Хранилище недоступно.")
        return

    user_id = message.from_user.id
    channels = _storage.get_channels(user_id)

    if not channels:
        await message.answer(
            "📋 У вас нет привязанных каналов.\n\n"
            "Добавьте бота в канал как админа и отправьте /link в канале.",
        )
        return

    lines = ["📋 <b>Ваши каналы:</b>\n"]
    default = _storage.get_default_channel(user_id)
    for ch_id in channels:
        marker = " ⭐" if ch_id == default else ""
        lines.append(f"  • <code>{ch_id}</code>{marker}")
    lines.append("\nСменить канал по умолчанию: /link <code>&lt;chat_id&gt;</code>")
    lines.append("Для отвязки: /unlink <code>&lt;chat_id&gt;</code>")

    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("unlink"))
async def cmd_unlink(message: Message):
    """/unlink <chat_id> — отвязывает канал."""
    if message.chat.type != "private":
        return

    if not _storage:
        await message.answer("⚠️ Хранилище недоступно.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "Использование: /unlink <code>&lt;chat_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        channel_id = int(args[1])
    except ValueError:
        await message.answer("❌ Неверный формат chat_id. Должно быть число.")
        return

    user_id = message.from_user.id
    if _storage.remove_channel(user_id, channel_id):
        await message.answer(f"✅ Канал <code>{channel_id}</code> отвязан.", parse_mode=ParseMode.HTML)
    else:
        await message.answer(f"❌ Канал <code>{channel_id}</code> не найден в ваших привязках.", parse_mode=ParseMode.HTML)
