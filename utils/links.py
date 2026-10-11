"""Разбор ссылок YouTube."""

import re
from typing import Optional

_YT_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?(?:[^ ]*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{6,})",
    re.IGNORECASE,
)


def find_youtube_link(text: str) -> Optional[str]:
    """Возвращает первую ссылку YouTube в тексте (нормализованную), если есть."""
    if not text or "youtu" not in text.lower():
        return None
    m = _YT_RE.search(text)
    if not m:
        return None
    return f"https://www.youtube.com/watch?v={m.group(1)}"
