"""Безопасный разбор callback_data (префикс + фиксированное число частей)."""

from typing import Optional


def parse_cb(data: Optional[str], prefix: str, n: int = 0) -> Optional[list[str]]:
    """Возвращает ровно n частей после префикса или None при несоответствии формата.

    Заменяет callback.data.split('|') — там нет защиты от подделанного
    callback (модифицированный клиент может прислать что угодно).
    """
    if not data or not data.startswith(prefix):
        return None
    rest = data[len(prefix):]
    parts = rest.split("|") if rest != "" else []
    if len(parts) != n:
        return None
    return parts


def to_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
