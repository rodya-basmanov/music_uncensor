"""Хендлеры /start и /help."""

from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.enums import ParseMode

router = Router()

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


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик /start."""
    await message.answer(START_TEXT, parse_mode=ParseMode.HTML)


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик /help."""
    await message.answer(HELP_TEXT, parse_mode=ParseMode.HTML)
