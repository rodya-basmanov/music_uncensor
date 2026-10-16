"""Хендлер /status — проверка прогресса задачи."""

import time

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
from utils.callback import parse_cb
from utils.escape import esc_html
from utils.safe import edit_or_send

router = Router()

PROGRESS_BAR_LENGTH = 10
_EXPORT_FMTS = ("txt", "json")
_REFRESH_MIN_INTERVAL = 1.0
_refresh_last: dict[int, float] = {}


def _format_progress_bar(processed: int, total: int) -> str:
    """Создаёт визуальную полосу прогресса (безопасно при total=0)."""
    if total <= 0:
        return "⬜" * PROGRESS_BAR_LENGTH + " 0%"
    pct = round(processed / total * 100)
    filled = max(0, min(PROGRESS_BAR_LENGTH, int(processed / total * PROGRESS_BAR_LENGTH)))
    return f"{'🟩' * filled}{'⬜' * (PROGRESS_BAR_LENGTH - filled)} {pct}%"


def _status_keyboard(task_id: str, done: bool) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{task_id}", style="primary")]
    if not done:
        row.append(InlineKeyboardButton(text="⏹ Отменить", callback_data=f"cancel_task|{task_id}", style="danger"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def _task_text(status, task_id: str) -> str:
    if status.done:
        state = "Завершена" if status.errors == 0 else "Завершена с ошибками"
        state_emoji = "🏁" if status.errors == 0 else "⚠️"
    else:
        state = "В процессе"
        state_emoji = "⏳"

    text = (
        f"{state_emoji} <b>Задача {task_id}</b>: {state}\n\n"
        f"{_format_progress_bar(status.processed, status.total)}\n"
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
            text += f"   • <code>{esc_html(ft[:60])}</code>\n"
        if len(status.failed_tracks) > 10:
            text += f"   ... и ещё {len(status.failed_tracks) - 10}"
    return text


@router.message(Command("status"))
async def cmd_status(message: Message):
    """/status <task_id> — показывает прогресс своей задачи."""
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
    if not status or status.user_id != message.from_user.id:
        await message.answer(
            "❌ Задача не найдена.\n\n"
            "Возможно, она уже завершена или такого ID не существует.",
            parse_mode=ParseMode.HTML,
        )
        return

    await message.answer(
        _task_text(status, task_id), parse_mode=ParseMode.HTML,
        reply_markup=_status_keyboard(task_id, status.done),
    )


@router.callback_query(F.data.startswith("refresh_"))
async def refresh_status(callback: CallbackQuery):
    """Обновление статуса задачи по callback (без дублей при «не изменено»)."""
    task_id = callback.data[len("refresh_"):]
    status = get_task_status(task_id)
    if not status or status.user_id != callback.from_user.id:
        await callback.answer("⌛ Устарело.", show_alert=True)
        return
    # Анти-спам: не дёргаем Telegram чаще раза в секунду на пользователя.
    uid = callback.from_user.id
    now = time.time()
    if now - _refresh_last.get(uid, 0.0) < _REFRESH_MIN_INTERVAL:
        await callback.answer("🔄 Уже обновляю…")
        return
    _refresh_last[uid] = now
    if len(_refresh_last) > 1000:
        _refresh_last.clear()
    await callback.answer("🔄 Обновляю...")
    await edit_or_send(
        callback.message, _task_text(status, task_id), parse_mode=ParseMode.HTML,
        reply_markup=_status_keyboard(task_id, status.done),
    )


@router.callback_query(F.data.startswith("export_menu|"))
async def cb_export_menu(callback: CallbackQuery):
    """Кнопка «📄 Скачать список» — выбор формата."""
    args = parse_cb(callback.data, "export_menu|", 1)
    if args is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    task_id = args[0]
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
    args = parse_cb(callback.data, "export|", 2)
    if args is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    fmt, task_id = args
    if fmt not in _EXPORT_FMTS:
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
