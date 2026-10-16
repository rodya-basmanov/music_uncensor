"""Хендлеры привязки каналов: /link, /channels, /unlink."""

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from utils.logger import get_logger
from utils.storage import UserStorage

log = get_logger("link_handler")

router = Router()

# Хранилище инжектится через middleware/глобальную переменную
_storage: UserStorage | None = None


def set_storage(storage: UserStorage) -> None:
    """Устанавливает хранилище (вызывается при инициализации бота)."""
    global _storage
    _storage = storage


async def _user_is_admin(bot, channel_id: int, user_id: int) -> bool:
    """Проверяет, что пользователь — админ/создатель канала."""
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        status = str(getattr(member, "status", ""))
        return status in ("creator", "administrator")
    except Exception as e:
        log.warning("user_admin_check_failed", channel_id=channel_id, user_id=user_id, error=str(e))
        return False


async def _bot_can_post(bot, channel_id: int) -> bool:
    """Проверяет, что бот — админ канала и может публиковать сообщения."""
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(channel_id, me.id)
        status = str(getattr(member, "status", ""))
        if status == "creator":
            return True
        if status == "administrator":
            return bool(getattr(member, "can_post_messages", False))
        return False
    except Exception as e:
        log.warning("bot_can_post_check_failed", channel_id=channel_id, error=str(e))
        return False


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
        if not _storage or not user_id:
            await message.answer("❌ Не удалось определить пользователя.")
            return
        if not await _bot_can_post(message.bot, channel_id):
            await message.answer(
                f"❌ Бот не является администратором канала <code>{channel_id}</code> "
                f"или не может публиковать сообщения.\n\n"
                f"Добавьте бота в канал администратором (с правом публикации) и попробуйте снова.",
                parse_mode=ParseMode.HTML,
            )
            return
        if not await _user_is_admin(message.bot, channel_id, user_id):
            await message.answer(
                "❌ Привязать канал может только его администратор. "
                "Убедитесь, что вы админ этого канала.",
                parse_mode=ParseMode.HTML,
            )
            return
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
    if user_id is None:
        await message.answer(
            "⚠️ Вы скрыты как администратор — не могу определить автора привязки.\n"
            "Привяжите канал из лички бота: <code>/link -100XXXXXXXXXX</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not _storage:
        await message.answer("⚠️ Хранилище недоступно.")
        return
    if not await _bot_can_post(message.bot, chat_id):
        await message.answer("❌ Бот не может публиковать сообщения в этом канале.")
        return
    if not await _user_is_admin(message.bot, chat_id, user_id):
        await message.answer("❌ Привязывать канал может только его администратор.")
        return
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

    lines = ["📡 <b>Ваши каналы</b>\n━━━━━━━━━━━━━━━━━━━━"]
    default = _storage.get_default_channel(user_id)
    for ch_id in channels:
        marker = "  ⭐ <i>по умолчанию</i>" if ch_id == default else ""
        lines.append(f"• <code>{ch_id}</code>{marker}")
    lines.append("\n🔗 /link <code>-100XXX</code> — сменить\n🗑 /unlink <code>-100XXX</code> — отвязать")

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
