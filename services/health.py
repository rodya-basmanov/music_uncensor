"""Heartbeat и алерты владельцу (первому, кто привязал канал)."""

import time
from typing import Awaitable, Callable, Optional

from utils.logger import get_logger

log = get_logger("health")

_started_at: float = time.time()
_last_beat: float = _started_at
_owner_getter: Optional[Callable[[], Optional[int]]] = None
_send_dm: Optional[Callable[[int, str], Awaitable[bool]]] = None
_last_alert: dict[str, float] = {}
_ALERT_COOLDOWN = 300.0


def mark_start() -> None:
    """Фиксирует момент старта и сбрасывает heartbeat."""
    global _started_at, _last_beat
    _started_at = time.time()
    _last_beat = _started_at


def beat() -> None:
    """Отмечает «живой» тик."""
    global _last_beat
    _last_beat = time.time()


def uptime() -> float:
    """Секунды с момента старта."""
    return time.time() - _started_at


def seconds_since_beat() -> float:
    """Секунды с последнего heartbeat."""
    return time.time() - _last_beat


def configure(
    owner_getter: Callable[[], Optional[int]],
    send_dm: Callable[[int, str], Awaitable[bool]],
) -> None:
    """Задаёт источник владельца и функцию отправки ЛС."""
    global _owner_getter, _send_dm
    _owner_getter = owner_getter
    _send_dm = send_dm


def get_owner() -> Optional[int]:
    """ID владельца (или None)."""
    if _owner_getter is None:
        return None
    try:
        return _owner_getter()
    except Exception:
        return None


async def alert_owner(text: str, key: str = "generic") -> bool:
    """Отправляет алерт владельцу в ЛС (с троттлингом по ключу)."""
    now = time.time()
    if now - _last_alert.get(key, 0.0) < _ALERT_COOLDOWN:
        return False
    owner = get_owner()
    if not owner or _send_dm is None:
        return False
    _last_alert[key] = now
    try:
        await _send_dm(owner, f"⚠️ <b>Алерт бота</b>\n\n{text}")
        return True
    except Exception as e:
        log.warning("alert_send_failed", error=str(e))
        return False
