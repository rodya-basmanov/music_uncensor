"""Разбор ссылок YouTube."""

import re
from typing import Optional

_YT_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?(?:[^ ]*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{6,})",
    re.IGNORECASE,
)

_SC_HOST = re.compile(r"https?://(?:www\.|m\.|on\.)?soundcloud\.com/", re.IGNORECASE)


def find_youtube_link(text: str) -> Optional[str]:
    """Возвращает первую ссылку YouTube в тексте (нормализованную), если есть."""
    if not text or "youtu" not in text.lower():
        return None
    m = _YT_RE.search(text)
    if not m:
        return None
    return f"https://www.youtube.com/watch?v={m.group(1)}"


def songlink_url(source_url: str) -> Optional[str]:
    """Строит ссылку song.link для источника (YouTube/SoundCloud) без обращения к API.

    YouTube → короткая song.link/y/<id>; SoundCloud → длинная song.link/<url>.
    """
    if not source_url:
        return None
    m = _YT_RE.search(source_url)
    if m:
        return f"https://song.link/y/{m.group(1)}"
    if _SC_HOST.search(source_url):
        return f"https://song.link/{source_url}"
    return None
