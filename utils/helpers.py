"""Вспомогательные утилиты."""

import hashlib
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Track:
    """Трек из VK плейлиста или свободный поисковый запрос."""
    artist: str
    title: str
    duration: int = 0
    query: str | None = None  # если задан — искать по этой строке как есть
    url: str = ""             # прямая ссылка на mp3 (VK)
    cover_url: str = ""       # прямая ссылка на обложку (VK)
    source: str = ""          # "vk" — трек получен из VK (качаем напрямую)
    force_meta: bool = False  # форсировать artist/title в метаданных (напр. из альбома)


def parse_track_line(line: str) -> Optional[Track]:
    """
    Парсит строку плейлиста.

    Поддерживает форматы:
      "Artist - Title [123]"  → artist/title
      "Artist – Title"        → artist/title (en-dash тоже)
      "любой свободный текст" → Track.query (поиск как есть, напр. мешап)

    Duration в конце "[123]" опциональна.
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

    if not line:
        return None

    # Разделяем по первому тире (обычный дефис или en/em dash в окружении пробелов)
    parts = re.split(r"\s+[-–—]\s+", line, maxsplit=1)
    if len(parts) == 2:
        artist, title = parts[0].strip(), parts[1].strip()
        if artist and title:
            return Track(artist=artist, title=title, duration=duration)

    # Нет разделителя — свободный поисковый запрос
    return Track(artist="", title=line, duration=duration, query=line)


def generate_hash(artist: str, title: str) -> str:
    """Генерирует SHA256-хеш по artist + title (нижний регистр)."""
    key = f"{artist.strip().lower()}{title.strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


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


def format_duration(seconds: int) -> str:
    """Форматирует секунды в читаемый вид: 5ч 30мин."""
    if seconds < 60:
        return f"{seconds} сек"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes}мин"
    return f"{minutes}мин"


def clean_title_for_display(title: str) -> str:
    """Очищает название для отображения: убирает только prod., feat., оставляет remix/slowed."""
    if not title:
        return ""
    cleaned = title
    cleaned = re.sub(r"\s*\([^)]*(?:prod\.?|produced by)[^)]*\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(?:feat\.?|ft\.?|featuring)\s+.+$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(?:prod\.?|produced by)\s+.+$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def translit(text: str) -> str:
    """Транслитерация кириллицы в латиницу (для поиска латинских названий)."""
    out: list[str] = []
    for ch in text or "":
        low = ch.lower()
        if low in _TRANSLIT:
            t = _TRANSLIT[low]
            out.append(t.upper() if ch.isupper() and t else t)
        else:
            out.append(ch)
    return "".join(out)


def crop_text(text: str, width: int) -> str:
    """Обрезает строку до width символов, добавляя «…» при усечении."""
    text = text or ""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[:width - 1].rstrip() + "…"

