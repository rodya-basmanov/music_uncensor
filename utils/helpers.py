"""Вспомогательные утилиты."""

import hashlib
import random
import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class Track:
    """Трек из VK плейлиста."""
    artist: str
    title: str
    duration: int = 0


# Пул User-Agent для ротации
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


def generate_hash(artist: str, title: str) -> str:
    """Генерирует SHA256-хеш по artist + title (нижний регистр)."""
    key = f"{artist.strip().lower()}{title.strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def random_ua() -> str:
    """Возвращает случайный User-Agent."""
    return random.choice(USER_AGENTS)


def parse_vk_playlist_url(url: str) -> Optional[Tuple[int, int, Optional[str]]]:
    """
    Парсит ссылку на плейлист ВК.
    
    Поддерживаемые форматы:
    - https://vk.com/music/playlist/OWNER_ID_PLAYLIST_ID
    - https://vk.com/music/playlist/OWNER_ID_PLAYLIST_ID_ACCESS_KEY
    - https://vk.com/audio?act=audio_playlist_OWNER_ID_PLAYLIST_ID
    - https://vk.com/music/album/OWNER_ID_ALBUM_ID_ACCESS_KEY
    
    Returns:
        (owner_id, playlist_id, access_key) или None
    """
    url = url.strip()
    
    # Формат: /music/playlist/OWNER_PLAYLIST или /music/album/OWNER_ALBUM
    match = re.search(
        r'vk\.com/music/(?:playlist|album)/(-?\d+)_(\d+)(?:_([a-zA-Z0-9]+))?',
        url
    )
    if match:
        owner_id = int(match.group(1))
        playlist_id = int(match.group(2))
        access_key = match.group(3)
        return (owner_id, playlist_id, access_key)
    
    # Формат: audio?act=audio_playlist_OWNER_PLAYLIST
    match = re.search(
        r'act=audio_playlist(-?\d+)_(\d+)(?:%2F|/|_)([a-zA-Z0-9]+)?',
        url
    )
    if match:
        owner_id = int(match.group(1))
        playlist_id = int(match.group(2))
        access_key = match.group(3)
        return (owner_id, playlist_id, access_key)
    
    return None
