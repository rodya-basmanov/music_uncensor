"""Разбор ссылок ВКонтакте на плейлисты/треки."""

import re
from dataclasses import dataclass
from typing import Optional

_VK_HOST = re.compile(r"(?:^|\W)(?:m\.)?vk\.(?:com|ru)/", re.IGNORECASE)

# music/playlist/-123_456_abc | music/album/...
_PLAYLIST_RE = re.compile(r"music/(?:playlist|album)[/\-]?(-?\d+)_(\d+)(?:_([0-9a-fA-F]+))?")
# audio-123_456_abc | /audio123_456
_TRACK_RE = re.compile(r"(?:^|/)audio-?(-?\d+)_(\d+)(?:_([0-9a-fA-F]+))?")
_ACCESS_QUERY_RE = re.compile(r"[?&](?:access_key|z)=([0-9a-fA-F]+)")


@dataclass
class VKPlaylistRef:
    owner_id: int
    playlist_id: int
    access_key: Optional[str] = None


@dataclass
class VKTrackRef:
    owner_id: int
    audio_id: int
    access_key: Optional[str] = None


def find_vk_link(text: str) -> Optional[str]:
    """Возвращает первую VK-ссылку в тексте, если есть."""
    if not text or ("vk.com" not in text.lower() and "vk.ru" not in text.lower()):
        return None
    for token in text.split():
        if _VK_HOST.search(token):
            return token.strip()
    return None


def parse_vk_link(text: str) -> Optional[VKPlaylistRef]:
    """Парсит VK-ссылку на ПЛЕЙЛИСТ."""
    if not text or not _VK_HOST.search(text):
        return None
    m = _PLAYLIST_RE.search(text)
    if not m:
        return None
    access_key = m.group(3)
    if not access_key:
        q = _ACCESS_QUERY_RE.search(text)
        if q:
            access_key = q.group(1)
    return VKPlaylistRef(owner_id=int(m.group(1)), playlist_id=int(m.group(2)), access_key=access_key)


def parse_vk_track_link(text: str) -> Optional[VKTrackRef]:
    """Парсит VK-ссылку на ОДИНОЧНЫЙ трек."""
    if not text or not _VK_HOST.search(text):
        return None
    m = _TRACK_RE.search(text)
    if not m:
        return None
    access_key = m.group(3)
    if not access_key:
        q = _ACCESS_QUERY_RE.search(text)
        if q:
            access_key = q.group(1)
    return VKTrackRef(owner_id=int(m.group(1)), audio_id=int(m.group(2)), access_key=access_key)
