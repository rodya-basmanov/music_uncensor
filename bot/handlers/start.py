"""Хендлеры /start и /help."""

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode

from utils.storage import UserStorage

router = Router()

_storage: UserStorage | None = None


@router.callback_query(F.data == "show_guide")
async def cb_show_guide(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(START_TEXT, parse_mode=ParseMode.HTML, reply_markup=_get_main_keyboard())


@router.callback_query(F.data == "show_help")
async def cb_show_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=_get_back_keyboard())


@router.callback_query(F.data == "show_link_help")
async def cb_show_link_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(LINK_HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=_get_back_keyboard())


@router.callback_query(F.data == "show_channels")
async def cb_show_channels(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    if _storage:
        channels = _storage.get_channels(user_id)
        if channels:
            default = _storage.get_default_channel(user_id)
            lines = ["📡 <b>Ваши каналы</b>\n━━━━━━━━━━━━━━━━━━━━"]
            for ch in channels:
                marker = "  ⭐ <i>по умолчанию</i>" if ch == default else ""
                lines.append(f"• <code>{ch}</code>{marker}")
            lines.append("\n🔗 /link <code>-100XXX</code> — сменить\n🗑 /unlink <code>-100XXX</code> — отвязать")
            await callback.message.answer("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=_get_back_keyboard())
        else:
            await callback.message.answer("📭 Нет привязанных каналов.\nДобавь бота в канал админом и отправь там /link.", reply_markup=_get_back_keyboard())
    else:
        await callback.message.answer("⚠️ Хранилище недоступно.", reply_markup=_get_back_keyboard())


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("🏠 <b>Главное меню</b>\n\nВыберите действие:", parse_mode=ParseMode.HTML, reply_markup=_get_main_keyboard())


def set_storage(storage: UserStorage) -> None:
    global _storage
    _storage = storage


def _get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔗 Привязать", callback_data="show_link_help"),
            InlineKeyboardButton(text="📊 Мои каналы", callback_data="show_channels"),
        ],
        [
            InlineKeyboardButton(text="📖 Инструкция", callback_data="show_guide"),
        ],
    ])


def _get_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
    ])


START_TEXT = """🎧 <b>VK Music Uncensor</b>
<i>Плейлисты ВК → треки без цензуры в твоём канале</i>
━━━━━━━━━━━━━━━━━━━━

<b>Как это работает</b>

1️⃣ Создай приватный канал и добавь бота админом
2️⃣ Отправь <b>в канале</b> команду /link — он привяжется
3️⃣ Пришли боту в личку одно из:

   🔗 <b>ссылку</b> на плейлист ВК
   📋 <b>список</b> треков (<code>Артист - Название</code>)
   🎵 <b>запрос</b> — покажу варианты на выбор

━━━━━━━━━━━━━━━━━━━━
📊 /status — статус   ·   📈 /stats — статистика
❓ /help — все команды"""

HELP_TEXT = """❓ <b>Справка</b>
━━━━━━━━━━━━━━━━━━━━

<b>Команды</b>
• /start — меню
• /link — привязать канал (<code>/link -100XXX</code>)
• /channels — мои каналы
• /unlink <code>-100XXX</code> — отвязать
• /status <code>id</code> — статус задачи
• /stats — статистика
• /cancel — отменить задачу

<b>Отправка треков</b>
• 🔗 ссылка на плейлист ВК — скачаю все треки
• 📋 текст/файл — по одному треку на строку
• 🎵 один запрос — покажу результаты, выберешь кнопкой"""

LINK_HELP_TEXT = """🔗 <b>Как привязать канал</b>
━━━━━━━━━━━━━━━━━━━━

<b>Автоматически</b>
1. Приватный канал
2. Добавь бота админом
3. Отправь <b>в канале</b> /link

<b>Вручную (в личке)</b>
<code>/link -100XXXXXXXXXX</code>"""


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(START_TEXT, parse_mode=ParseMode.HTML, reply_markup=_get_main_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=_get_back_keyboard())