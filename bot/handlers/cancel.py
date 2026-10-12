"""Хендлер /cancel — отмена текущей задачи."""

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from services.processor import cancel_task, cancel_user_task, get_task_status, is_user_busy

router = Router()


@router.callback_query(F.data.startswith("cancel_task|"))
async def cb_cancel_task(callback: CallbackQuery):
    """Кнопка «⏹ Отменить» в живом статусе."""
    task_id = callback.data.split("|", 1)[1]
    status = get_task_status(task_id)
    if not status:
        await callback.answer("Задача не найдена.", show_alert=True)
        return
    if callback.from_user.id != status.user_id:
        await callback.answer("Это не ваша задача.", show_alert=True)
        return
    ok = cancel_task(task_id)
    await callback.answer("⏹ Отменяю…" if ok else "Уже завершена")


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
