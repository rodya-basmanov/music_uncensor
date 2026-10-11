"""Хендлеры /start и /help."""

import os
import tempfile

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.enums import ParseMode

from utils.storage import UserStorage

router = Router()

_storage: UserStorage | None = None

VK_SCRIPT = """(async () => {
    const d = ms => new Promise(r => setTimeout(r, ms));
    function c(t) {
        if (!t) return '';
        return t
            .replace(/\\s*\\([^)]*(?:feat\\.?|ft\\.?|featuring|prod\\.?|produced by|remix|live|explicit|censor|radio edit)[^)]*\\)\\s*/gi, '')
            .replace(/\\s*(?:feat\\.?|ft\\.?|featuring)\\s+[^,\\n;|()]+/gi, '')
            .replace(/\\s*(?:prod\\.?|produced by)\\s+[^,\\n;|()]+/gi, '')
            .replace(/\\s*(?:x|&|vs\\.?|with)\\s+[^,\\n;|()]+/gi, '')
            .replace(/,\\s+[^,]+$/g, '')
            .replace(/\\s+/g, ' ')
            .replace(/[\\s,\\.]+$/, '')
            .trim();
    }
    async function l() {
        let h = 0, n = 0;
        while (n < 3) {
            window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
            await d(1500);
            const nh = document.documentElement.scrollHeight;
            if (nh === h) n++;
            else { n = 0; h = nh; }
        }
    }
    function p(t) {
        if (!t) return 0;
        const m = t.match(/(\\d+):(\\d{2})/);
        return m ? parseInt(m[1]) * 60 + parseInt(m[2]) : 0;
    }
    function f(el) {
        let dur = el?.getAttribute?.('data-duration');
        if (dur && !isNaN(dur)) return parseInt(dur);
        if (el) {
            const root = el.parentElement?.parentElement?.parentElement?.parentElement?.parentElement;
            if (root) {
                const de = root.querySelector('[class*="vkitAudioPlayerPlaybackProgressTime__text"]');
                if (de) { const d = p(de.textContent?.trim()); if (d) return d; }
            }
        }
        if (el) {
            const row = el.closest?.('.audio_row');
            if (row) { const de = row.querySelector('.audio_row__duration'); if (de) return p(de.textContent?.trim()); }
        }
        return 0;
    }
    function t() {
        const tracks = [];
        const rows = document.querySelectorAll('[data-testid="MusicTrackRow_Info"]');
        if (rows.length) {
            for (const row of rows) {
                try {
                    let title = row.children[0]?.textContent?.trim() || '';
                    let artist = row.children[1]?.textContent?.trim() || '';
                    let dur = f(row);
                    if (artist || title) {
                        let line = artist + ' - ' + title;
                        if (dur) line += ' [' + dur + ']';
                        tracks.push(line);
                    }
                } catch(e) {}
            }
        } else {
            const rows = document.querySelectorAll('.audio_row__performer_title');
            for (const row of rows) {
                try {
                    let artist = row.querySelector('.audio_row__performers')?.textContent?.trim() || '';
                    let title = row.querySelector('._audio_row__title a')?.textContent?.trim() || '';
                    let dur = f(row);
                    if (artist || title) {
                        let line = artist + ' - ' + title;
                        if (dur) line += ' [' + dur + ']';
                        tracks.push(line);
                    }
                } catch(e) {}
            }
        }
        return tracks.filter(x => x && x !== ' - ');
    }
    function g() {
        return document.querySelector('.AudioPlaylistSnippet__title--main')?.textContent?.trim()
            || document.querySelector('.ui_tab.ui_tab_sel')?.textContent?.trim()
            || 'VK_Playlist';
    }
    function s(fn, c) {
        const blob = new Blob([c.replace(/\\n/g, '\\r\\n')], { type: 'text/plain' });
        const link = document.createElement('a');
        link.download = fn;
        link.href = URL.createObjectURL(blob);
        link.click();
        URL.revokeObjectURL(link.href);
    }
    try {
        await l();
        const list = t();
        if (!list.length) { alert('Треки не найдены'); return; }
        const safe = g().replace(/[\\\\/:*?"<>|]/g, '_').slice(0, 100);
        s(safe + '.txt', list.join('\\n'));
        alert('Готово! ' + list.length + ' треков');
    } catch(e) { alert('Ошибка: ' + e.message); }
})();"""

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


@router.callback_query(F.data == "get_script")
async def cb_get_script(callback: CallbackQuery):
    await callback.answer()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".js", mode="w", encoding="utf-8") as f:
        f.write(VK_SCRIPT)
        tmp_path = f.name

    try:
        await callback.message.answer(
            "📜 <b>Скрипт для экспорта VK</b>\\n\\n"
            "📋 <b>Как использовать:</b>\\n"
            "1. Открой плейлист ВКонтакте\\n"
            "2. F12 → Console → вставь скрипт из файла → Enter\\n"
            "3. Скачается .txt файл",
            parse_mode=ParseMode.HTML,
        )
        doc = FSInputFile(tmp_path)
        await callback.message.answer_document(doc, caption="Скрипт для VK")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)[:100]}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def set_storage(storage: UserStorage) -> None:
    global _storage
    _storage = storage

def _get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📜 Скрипт VK", callback_data="get_script"),
            InlineKeyboardButton(text="📖 Инструкция", callback_data="show_guide"),
        ],
        [
            InlineKeyboardButton(text="🔗 Привязать", callback_data="show_link_help"),
            InlineKeyboardButton(text="📊 Мои каналы", callback_data="show_channels"),
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