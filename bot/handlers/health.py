"""Хендлер /health — состояние бота (для владельца)."""

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from core.cache_manager import CacheManager
from services import health, processor
from utils.logger import get_logger
from utils.storage import UserStorage

log = get_logger("health_handler")

router = Router()

_cache_mgr: CacheManager | None = None
_storage: UserStorage | None = None
_ytdlp_proxy: str = ""
_pot_provider: str = ""
_vk_token: str = ""


def set_dependencies(
    cache_mgr: CacheManager,
    storage: UserStorage,
    ytdlp_proxy: str,
    pot_provider: str,
    vk_token: str,
) -> None:
    """Прокидывает зависимости из main."""
    global _cache_mgr, _storage, _ytdlp_proxy, _pot_provider, _vk_token
    _cache_mgr = cache_mgr
    _storage = storage
    _ytdlp_proxy = ytdlp_proxy
    _pot_provider = pot_provider
    _vk_token = vk_token


def _fmt_uptime(sec: float) -> str:
    sec = int(sec)
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    if d:
        return f"{d}д {h}ч {m}м"
    if h:
        return f"{h}ч {m}м"
    if m:
        return f"{m}м {s}с"
    return f"{s}с"


@router.message(Command("health"))
async def cmd_health(message: Message):
    """/health — аптайм, heartbeat, задачи, кэш, конфиг."""
    if message.chat.type != "private":
        return

    owner = _storage.get_owner() if _storage else None
    uid = message.from_user.id if message.from_user else None
    if owner is not None and uid != owner:
        await message.answer("ℹ️ Команда доступна владельцу.")
        return

    stats = processor.get_stats()
    active = processor.active_task_count()
    since_beat = health.seconds_since_beat()

    cache_line = "—"
    if _cache_mgr is not None:
        cs = _cache_mgr.get_cache_stats()
        cache_line = f"{cs['size_gb']:.2f} ГБ · {cs['files']} файлов"

    vk_map = {
        "ok": "✅ активна",
        "expired": "❌ токен истёк",
        "error": "⚠️ ошибка сети",
        "missing": "❌ не задан",
        "unknown": "…",
    }
    vk_line = vk_map.get(processor.vk_token_status(), processor.vk_token_status())

    tg = health.telegram_mode()
    if tg == "proxy":
        tg_line = f"через прокси (<code>{health.telegram_proxy()}</code>)"
    elif tg == "direct":
        tg_line = "прямое соединение"
    else:
        tg_line = "—"

    proxy_line = _ytdlp_proxy or "❌ нет"
    pot_line = "✅ настроен" if _pot_provider else "❌ нет"

    text = (
        "🩺 <b>Здоровье бота</b>\n\n"
        f"⏱ Аптайм: <b>{_fmt_uptime(health.uptime())}</b>\n"
        f"💓 Heartbeat: {since_beat:.0f}с назад\n"
        f"🔧 Активных задач: <b>{active}</b>\n\n"
        f"📊 Отправлено: <b>{stats.get('total_sent', 0)}</b> "
        f"(кеш: {stats.get('total_cached', 0)}, не найдено: {stats.get('total_not_found', 0)})\n"
        f"💾 Кэш: {cache_line}\n\n"
        f"📡 Telegram: {tg_line}\n"
        f"🎵 VK-токен: {vk_line}\n"
        f"🌐 Прокси (yt-dlp): <code>{proxy_line}</code>\n"
        f"🔑 PO-token: {pot_line}"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)
