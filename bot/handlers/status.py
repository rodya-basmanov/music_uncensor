"""Хендлер /status — проверка прогресса задачи."""

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from services.processor import export_task, get_task_status

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
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{task_id}", style="primary"), InlineKeyboardButton(text="⏹ Отменить", callback_data=f"cancel_task|{task_id}", style="danger")],
    ])

    if status.done:
        state = "Завершена" if status.errors == 0 else "Завершена с ошибками"
        state_emoji = "🏁" if status.errors == 0 else "⚠️"
    else:
        state = "В процессе"
        state_emoji = "⏳"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{task_id}", style="primary"), InlineKeyboardButton(text="⏹ Отменить", callback_data=f"cancel_task|{task_id}", style="danger")],
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
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{task_id}", style="primary"), InlineKeyboardButton(text="⏹ Отменить", callback_data=f"cancel_task|{task_id}", style="danger")],
    ])

    if status.done:
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


@router.callback_query(F.data.startswith("export_menu|"))
async def cb_export_menu(callback: CallbackQuery):
    """Кнопка «📄 Скачать список» — выбор формата."""
    task_id = callback.data.split("|", 1)[1]
    status = get_task_status(task_id)
    if not status or status.user_id != callback.from_user.id:
        await callback.answer("Недоступно.", show_alert=True)
        return
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📄 TXT", callback_data=f"export|txt|{task_id}"),
        InlineKeyboardButton(text="🗂 JSON", callback_data=f"export|json|{task_id}"),
    ]])
    await callback.message.answer(
        "📄 <b>Скачать список</b>\nВыбери формат:", parse_mode=ParseMode.HTML, reply_markup=kb,
    )


@router.callback_query(F.data.startswith("export|"))
async def cb_export(callback: CallbackQuery):
    """Отправляет выбранный формат списка (только успешно скачанные)."""
    try:
        _, fmt, task_id = callback.data.split("|")
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    status = get_task_status(task_id)
    if not status or status.user_id != callback.from_user.id:
        await callback.answer("Недоступно.", show_alert=True)
        return
    data = export_task(task_id)
    if not data:
        await callback.answer("Нет успешно скачанных треков.", show_alert=True)
        return
    await callback.answer("Отправляю…")
    path = data["txt"] if fmt == "txt" else data["json"]
    label = "TXT" if fmt == "txt" else "JSON"
    try:
        await callback.message.answer_document(
            FSInputFile(path), caption=f"📄 Список ({data['count']}) · {label}",
        )
    except Exception:
        await callback.message.answer("❌ Не удалось отправить файл.")
