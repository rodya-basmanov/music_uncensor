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
    /link — получает chat_id текущего чата и привязывает канал.
    
    Работает в каналах/группах, где бот — админ.
    В ЛС показывает инструкцию.
    """
    chat_id = message.chat.id
    chat_type = message.chat.type

    # Если команда в ЛС — показываем инструкцию
    if chat_type == "private":
        await message.answer(
            "ℹ️ Эту команду нужно отправить <b>в канале</b>, где бот — администратор.\n\n"
            "Бот ответит числовым chat_id канала, который нужно использовать "
            "при отправке плейлиста.",
            parse_mode=ParseMode.HTML,
        )
        return

    # В канале/группе — сохраняем привязку
    # sender_chat для каналов, from_user для групп
    user_id = None
    if message.from_user:
        user_id = message.from_user.id
    elif message.sender_chat:
        # В каналах from_user может быть None, 
        # сообщаем chat_id без привязки к пользователю
        pass

    if _storage and user_id:
        _storage.add_channel(user_id, chat_id)

    await message.answer(
        f"✅ Chat ID этого канала:\n"
        f"<code>{chat_id}</code>\n\n"
        f"Скопируйте и отправьте боту в ЛС вместе со ссылкой на плейлист.",
        parse_mode=ParseMode.HTML,
    )
    log.info("link_command", chat_id=chat_id, user_id=user_id)


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
    for ch_id in channels:
        lines.append(f"  • <code>{ch_id}</code>")
    lines.append("\nДля отвязки: /unlink <code>&lt;chat_id&gt;</code>")

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
