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


def parse_track_line(line: str) -> Optional[Track]:
    """
    Парсит строку вида 'Artist - Title [duration_sec]'.
    Формат script.js: Artist - Title [123].
    Duration опционально.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # Извлекаем duration из [число] в конце строки
    duration = 0
    duration_match = re.search(r'\[(\d+)\]\s*$', line)
    if duration_match:
        duration = int(duration_match.group(1))
        line = line[:duration_match.start()].strip()

    # Пробуем разделить по первому ' - '
    parts = line.split(" - ", 1)
    if len(parts) == 2:
        artist, title = parts[0].strip(), parts[1].strip()
        if artist and title:
            return Track(artist=artist, title=title, duration=duration)
    return None


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



def clean_artist_for_search(artist: str) -> str:
    """Очищает имя артиста для поиска: убирает соавторов после запятой."""
    if not artist:
        return ""
    # Убираем соавторов через запятую (оставляем первого)
    cleaned = re.sub(r",\s+.+$", "", artist)
    # Убираем feat./ft./featuring и всё после
    cleaned = re.sub(r"\s*(?:feat\.?|ft\.?|featuring)\s+.+$", "", cleaned, flags=re.IGNORECASE)
    # Убираем prod./produced by и всё после
    cleaned = re.sub(r"\s*(?:prod\.?|produced by)\s+.+$", "", cleaned, flags=re.IGNORECASE)
    # Убираем x/& между артистами
    cleaned = re.sub(r"\s*(?:\sx\s|&\s).+$", "", cleaned)
    return cleaned.strip()


def clean_title_for_search(title: str) -> str:
    """Очищает название трека для поиска: убирает (feat...), (prod...), prod. by."""
    if not title:
        return ""
    cleaned = title
    # Убираем скобки с feat/prod/remix/live/explicit
    cleaned = re.sub(r"\s*\([^)]*(?:feat\.?|ft\.?|featuring|prod\.?|produced by|remix|live|explicit|censor|radio edit)[^)]*\)", "", cleaned, flags=re.IGNORECASE)
    # Убираем feat./ft. и всё после (в названии)
    cleaned = re.sub(r"\s*(?:feat\.?|ft\.?|featuring)\s+.+$", "", cleaned, flags=re.IGNORECASE)
    # Убираем prod./produced by и всё после (в названии)
    cleaned = re.sub(r"\s*(?:prod\.?|produced by)\s+.+$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

