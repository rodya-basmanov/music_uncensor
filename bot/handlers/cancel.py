"""Хендлер /cancel — отмена текущей задачи."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode

from services.processor import cancel_user_task, is_user_busy

router = Router()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message):
    """/cancel — отменяет текущую задачу обработки плейлиста."""
    if message.chat.type != "private":
        return

    user_id = message.from_user.id if message.from_user else None
    if not user_id:
        return

    if not is_user_busy(user_id):
        await message.answer(
            "ℹ️ У вас нет активной задачи для отмены.",
            parse_mode=ParseMode.HTML,
        )
        return

    task_id = cancel_user_task(user_id)
    if task_id:
        await message.answer(
            f"⏹️ <b>Задача отменена!</b>\n\n"
            f"ID: <code>{task_id}</code>\n\n"
            f"Часть треков уже была отправлена в канал.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(
            "⚠️ Не удалось отменить задачу. Попробуйте позже.",
            parse_mode=ParseMode.HTML,
        )