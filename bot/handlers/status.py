"""Хендлер /status — проверка прогресса задачи."""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode

from services.processor import get_task_status

router = Router()

PROGRESS_BAR_LENGTH = 10


def _format_progress_bar(processed: int, total: int) -> str:
    """Создаёт визуальную полосу прогресса."""
    filled = int(processed / total * PROGRESS_BAR_LENGTH)
    empty = PROGRESS_BAR_LENGTH - filled
    bar = "🟩" * filled + "⬜" * empty
    pct = round(processed / total * 100) if total > 0 else 0
    return f"{bar} {pct}%"


@router.message(Command("status"))
async def cmd_status(message: Message):
    """/status <task_id> — показывает прогресс задачи."""
    if message.chat.type != "private":
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "📋 Использование: /status <code>&lt;task_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    task_id = args[1].strip()
    status = get_task_status(task_id)

    if not status:
        await message.answer(
            "❌ Задача не найдена.\n\n"
            "Возможно, она уже завершена или такого ID не существует.",
            parse_mode=ParseMode.HTML,
        )
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{task_id}")],
    ])

    if status.done:
        emoji = "✅" if status.errors == 0 else "⚠️"
        state = "Завершена" if status.errors == 0 else "Завершена с ошибками"
        state_emoji = "🏁" if status.errors == 0 else "⚠️"
    else:
        state = "В процессе"
        state_emoji = "⏳"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{task_id}")],
        ])

    progress_bar = _format_progress_bar(status.processed, status.total)

    text = (
        f"{state_emoji} <b>Задача {task_id}</b>: {state}\n\n"
        f"{progress_bar}\n"
        f"📊 Обработано: {status.processed}/{status.total}\n\n"
        f"📈 <b>Результат:</b>\n"
        f"   ✅ Отправлено: {status.sent}\n"
        f"   ⚡ Из кэша: {status.cached}\n"
        f"   ❌ Не найдено: {status.not_found}\n"
        f"   ⚠️ Ошибок: {status.errors}\n"
    )

    if status.failed_tracks and status.done:
        text += f"\n📝 <b>Не найдены ({len(status.failed_tracks)}):</b>\n"
        for ft in status.failed_tracks[:10]:
            text += f"   • <code>{ft[:60]}</code>\n"
        if len(status.failed_tracks) > 10:
            text += f"   ... и ещё {len(status.failed_tracks) - 10}"

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@router.callback_query(F.data.startswith("refresh_"))
async def refresh_status(callback: CallbackQuery):
    """Обновление статуса задачи по callback."""
    await callback.answer("🔄 Обновляю...")
    task_id = callback.data.replace("refresh_", "")

    status = get_task_status(task_id)
    if not status:
        await callback.message.edit_text(
            "❌ Задача не найдена.",
            parse_mode=ParseMode.HTML,
        )
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{task_id}")],
    ])

    if status.done:
        emoji = "✅" if status.errors == 0 else "⚠️"
        state = "Завершена" if status.errors == 0 else "Завершена с ошибками"
        state_emoji = "🏁" if status.errors == 0 else "⚠️"
    else:
        state = "В процессе"
        state_emoji = "⏳"

    progress_bar = _format_progress_bar(status.processed, status.total)

    text = (
        f"{state_emoji} <b>Задача {task_id}</b>: {state}\n\n"
        f"{progress_bar}\n"
        f"📊 Обработано: {status.processed}/{status.total}\n\n"
        f"📈 <b>Результат:</b>\n"
        f"   ✅ Отправлено: {status.sent}\n"
        f"   ⚡ Из кэша: {status.cached}\n"
        f"   ❌ Не найдено: {status.not_found}\n"
        f"   ⚠️ Ошибок: {status.errors}\n"
    )

    if status.failed_tracks and status.done:
        text += f"\n📝 <b>Не найдены ({len(status.failed_tracks)}):</b>\n"
        for ft in status.failed_tracks[:10]:
            text += f"   • <code>{ft[:60]}</code>\n"
        if len(status.failed_tracks) > 10:
            text += f"   ... и ещё {len(status.failed_tracks) - 10}"

    try:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
