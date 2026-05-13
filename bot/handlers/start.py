"""Хендлеры /start и /help."""

import os

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.enums import ParseMode

from utils.storage import UserStorage

router = Router()

_storage: UserStorage | None = None

# Путь к script.js относительно корня проекта
SCRIPT_JS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "script.js")


# === Callback Query Handlers ===

@router.callback_query(F.data == "show_guide")
async def cb_show_guide(callback: CallbackQuery):
    """Показать полную инструкцию."""
    await callback.answer()
    await callback.message.answer(START_TEXT, parse_mode=ParseMode.HTML, reply_markup=_get_main_keyboard())


@router.callback_query(F.data == "show_help")
async def cb_show_help(callback: CallbackQuery):
    """Показать справку."""
    await callback.answer()
    await callback.message.answer(HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=_get_back_keyboard())


@router.callback_query(F.data == "show_link_help")
async def cb_show_link_help(callback: CallbackQuery):
    """Показать помощь по привязке канала."""
    await callback.answer()
    await callback.message.answer(LINK_HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=_get_back_keyboard())


@router.callback_query(F.data == "show_channels")
async def cb_show_channels(callback: CallbackQuery):
    """Показать список каналов."""
    await callback.answer()
    user_id = callback.from_user.id

    if _storage:
        channels = _storage.get_channels(user_id)
        if channels:
            default = _storage.get_default_channel(user_id)
            lines = ["📊 <b>Ваши каналы:</b>\n"]
            for ch_id in channels:
                marker = " ⭐" if ch_id == default else ""
                lines.append(f"  • <code>{ch_id}</code>{marker}")
            lines.append("\n<b>Управление:</b>\n")
            lines.append("• /link <code>-100XXX</code> — привязать\n")
            lines.append("• /unlink <code>-100XXX</code> — отвязать")
            text = "\n".join(lines)
        else:
            text = "📊 У вас нет привязанных каналов.\n\nДобавьте бота в канал как админа и отправьте /link в канале."
    else:
        text = "⚠️ Хранилище недоступно."

    await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=_get_back_keyboard())


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery):
    """Вернуться в главное меню."""
    await callback.answer()
    text = "🏠 <b>Главное меню</b>\n\nВыберите действие:"
    await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=_get_main_keyboard())


@router.callback_query(F.data == "get_script")
async def cb_get_script(callback: CallbackQuery):
    """Отправить скрипт для копирования."""
    await callback.answer()

    try:
        if os.path.exists(SCRIPT_JS_PATH):
            with open(SCRIPT_JS_PATH, "r", encoding="utf-8") as f:
                script_content = f.read()

            # Если слишком длинный — отправляем как файл
            if len(script_content) > 4000:
                await callback.message.answer_document(
                    document=FSInputFile(SCRIPT_JS_PATH),
                    caption="📜 <b>Скрипт для экспорта VK</b>\n\n"
                            "📋 <b>Инструкция:</b>\n"
                            "1. Скачай файл\n"
                            "2. Открой плейлист ВКонтакте\n"
                            "3. F12 → Console → вставь код → Enter\n"
                            "4. Скрипт скачает <code>.txt</code> с треками",
                    parse_mode=ParseMode.HTML,
                )
            else:
                # Иначе — отправляем текстом для копирования
                escaped = script_content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await callback.message.answer(
                    "📜 <b>Скрипт для экспорта VK</b>\n\n"
                    "Нажми на код — появится «Копировать»:\n\n"
                    "<pre>" + escaped + "</pre>\n\n"
                    "📋 <b>Как использовать:</b>\n"
                    "1. Открой плейлист ВКонтакте\n"
                    "2. F12 → Console → вставь код → Enter\n"
                    "3. Скрипт скачает <code>.txt</code> с треками",
                    parse_mode=ParseMode.HTML,
                )

            await callback.answer("📜 Готово!", show_alert=False)
        else:
            await callback.message.answer("⚠️ Файл скрипта не найден.", reply_markup=_get_back_keyboard())
    except Exception as e:
        await callback.message.answer(f"⚠️ Ошибка: {str(e)[:100]}", reply_markup=_get_back_keyboard())


def set_storage(storage: UserStorage) -> None:
    """Устанавливает хранилище (вызывается при инициализации бота)."""
    global _storage
    _storage = storage


def _get_main_keyboard() -> InlineKeyboardMarkup:
    """Главная клавиатура быстрого доступа."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Скрипт для VK", callback_data="get_script")],
        [InlineKeyboardButton(text="📋 Инструкция", callback_data="show_guide")],
        [InlineKeyboardButton(text="🔗 Привязать канал", callback_data="show_link_help")],
        [InlineKeyboardButton(text="📊 Мои каналы", callback_data="show_channels")],
    ])


def _get_back_keyboard() -> InlineKeyboardMarkup:
    """Кнопка возврата в главное меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
    ])

START_TEXT = """🎧 <b>VK Uncensor Bridge</b> — экспорт плейлистов без цензуры

Бот находит <b>оригинальные (незацензуренные)</b> версии треков на mp3party.net и доставляет их в ваш приватный Telegram-канал.

<b>Как использовать:</b>
1️⃣ Создайте приватный канал в Телеграме
2️⃣ Добавьте бота в канал как администратора
   (права: «Отправка сообщений»)
3️⃣ Отправьте <b>в канале</b> команду: /link
4️⃣ Бот ответит числовым chat_id канала
5️⃣ Откройте VK-плейлист в браузере, нажмите <b>F12</b>, вставьте <code>script.js</code> в консоль и получите <b>.txt</b> файл
6️⃣ Отправьте боту <b>в ЛС</b> этот файл (или скопируйте текст):

<b>Формат файла / текста:</b>
<code>-1001234567890
Title - Artist
Title 2 - Artist 2</code>

<i>script.js экспортирует в формате Title — Artist (чистка от feat./prod. включена)</i>

📊 Статус задачи: /status &lt;task_id&gt;
📋 Мои каналы: /channels
📈 Статистика: /stats
⏹️ Отменить задачу: /cancel
❓ Помощь: /help

⚠️ <b>Дисклеймер:</b> Бот предоставляется «как есть» в образовательных целях. Кэш автоматически очищается каждые 7 дней. Бот не хранит персональные данные."""

HELP_TEXT = """❓ <b>Справка — VK Uncensor Bridge</b>

<b>Команды:</b>
• /start — приветствие и инструкция
• /help — эта справка
• /link — получить chat_id канала (отправлять <b>в канале</b>)
• /channels — список привязанных каналов
• /unlink &lt;chat_id&gt; — отвязать канал
• /status &lt;task_id&gt; — статус обработки плейлиста
• /stats — статистика использования
• /cancel — отменить текущую задачу

<b>Как отправить плейлист:</b>
1. Экспортируйте плейлист из VK через F12 + script.js (получите .txt)
2. Отправьте боту файл или скопируйте текст в ЛС:
<code>-100XXXXXXXXXXX
Название трека - Исполнитель
Название трека 2 - Исполнитель 2</code>

<b>Лимиты:</b>
• 1 задача на пользователя одновременно
• До 100 треков в плейлисте
• Задержка 2-4 сек между треками (анти-бан)

<b>Кэш:</b>
• Повторные запросы того же трека — мгновенная отправка
• Кэш очищается каждые 7 дней"""

LINK_HELP_TEXT = """🔗 <b>Как привязать канал</b>

<b>Способ 1 (автоматический):</b>
1. Создайте приватный канал в Telegram
2. Добавьте бота как администратора
3. Отправьте /link <b>в канале</b>

<b>Способ 2 (ручной):</b>
1. Получите chat_id канала
2. Отправьте боту: <code>/link -100XXXXXXXXXX</code>

ℹ️ Бот должен быть админом канала с правом отправки сообщений."""

CHANNELS_TEXT = """📊 <b>Ваши каналы</b>

Чтобы увидеть список привязанных каналов, нажмите /channels в личных сообщениях с ботом.

<b>Добавить канал:</b> Отправьте /link в канале, где бот — администратор."""


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик /start."""
    await message.answer(START_TEXT, parse_mode=ParseMode.HTML, reply_markup=_get_main_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик /help."""
    await message.answer(HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=_get_back_keyboard())
