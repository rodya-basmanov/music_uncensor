"""Хендлер /status — проверка прогресса задачи."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode

from services.processor import get_task_status

router = Router()


@router.message(Command("status"))
async def cmd_status(message: Message):
    """/status <task_id> — показывает прогресс задачи."""
    if message.chat.type != "private":
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "Использование: /status <code>&lt;task_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    task_id = args[1].strip()
    status = get_task_status(task_id)

    if not status:
        await message.answer(
            f"❌ Задача <code>{task_id}</code> не найдена.\n"
            f"Возможно, она уже завершена и удалена из памяти.",
            parse_mode=ParseMode.HTML,
        )
        return

    if status.done:
        emoji = "✅" if status.errors == 0 else "⚠️"
        state = "Завершена"
    else:
        emoji = "⏳"
        state = f"В процессе ({status.processed}/{status.total})"

    text = (
        f"{emoji} Задача <code>{task_id}</code>: {state}\n\n"
        f"📊 Прогресс: {status.processed}/{status.total}\n"
        f"  ✅ Отправлено: {status.sent}\n"
        f"  ⚡ Из кэша: {status.cached}\n"
        f"  ❌ Не найдено: {status.not_found}\n"
        f"  ⚠️ Ошибок: {status.errors}"
    )

    if status.failed_tracks and status.done:
        text += "\n\n❌ Не найдены:\n"
        for ft in status.failed_tracks[:15]:
            text += f"  • {ft}\n"

    await message.answer(text, parse_mode=ParseMode.HTML)
