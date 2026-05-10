"""Скрапер SoundCloud через yt-dlp — основной источник uncensored треков.

yt-dlp поддерживает поиск SoundCloud: scsearch:N:query
Не требует VPN, работает в РФ.
Артисты часто заливают uncensored версии напрямую.

Стратегия: сначала ищем scsearch5, выбираем лучший по duration,
затем скачиваем конкретный трек по URL.
"""

import os
import subprocess
from typing import Optional, List, Dict

from utils.logger import get_logger

log = get_logger("soundcloud")


def _get_ffmpeg_args() -> List[str]:
    """Возвращает --ffmpeg-location аргумент если системный ffmpeg не найден."""
    try:
        import shutil
        if not shutil.which("ffmpeg"):
            try:
                import imageio_ffmpeg
                return ["--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe()]
            except ImportError:
                pass
    except Exception:
        pass
    return []


def _search_tracks(query: str, count: int = 5) -> List[Dict]:
    """Ищет треки на SoundCloud через yt-dlp flat search.

    Returns:
        Список словарей: {url, title, duration, uploader}
    """
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--print", "%(webpage_url)s|%(title)s|%(duration)s|%(uploader)s",
        f"scsearch{count}:{query}",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            log.warning("soundcloud_search_failed", query=query, stderr=result.stderr[:200])
            return []

        tracks = []
        for line in result.stdout.strip().split("\n"):
            if not line or "|" not in line:
                continue
            parts = line.split("|", 3)
            if len(parts) < 3:
                continue
            url, title, dur_str = parts[0], parts[1], parts[2]
            uploader = parts[3] if len(parts) > 3 else ""
            try:
                dur = float(dur_str)
            except (ValueError, TypeError):
                dur = 0
            tracks.append({"url": url, "title": title, "duration": dur, "uploader": uploader})

        return tracks

    except subprocess.TimeoutExpired:
        log.warning("soundcloud_search_timeout", query=query)
        return []
    except Exception as e:
        log.warning("soundcloud_search_error", query=query, error=str(e))
        return []


def _pick_best(tracks: List[Dict], expected_duration: int = 0, artist: str = "") -> Optional[Dict]:
    """Выбирает лучший трек из результатов поиска.

    Приоритет:
    1. Трек от артиста (uploader совпадает) + не remix + близкий duration
    2. Трек от артиста + не remix
    3. Не remix + близкий duration
    4. Не remix
    5. Любой
    """
    if not tracks:
        return None

    artist_lower = artist.lower().strip()

    # Фильтруем slowed/reverb/sped если есть нормальные
    normal = [t for t in tracks if not _is_remix(t["title"])]
    remix = [t for t in tracks if _is_remix(t["title"])]

    pool = normal if normal else tracks

    # Треки от самого артиста (uploader совпадает с artist)
    by_artist = [t for t in pool if _uploader_matches(t.get("uploader", ""), artist_lower)]

    # Приоритет 1: от артиста + близкий duration
    if by_artist and expected_duration > 0:
        best = _closest_by_duration(by_artist, expected_duration)
        if best:
            return best

    # Приоритет 2: от артиста (без duration фильтра)
    if by_artist:
        return by_artist[0]

    # Приоритет 3: не remix + близкий duration
    if expected_duration > 0:
        best = _closest_by_duration(pool, expected_duration)
        if best:
            return best

    # Приоритет 4: не remix
    return pool[0] if pool else tracks[0]


def _is_remix(title: str) -> bool:
    """Проверяет является ли трек ремиксом/slowed/sped версией."""
    lower = title.lower()
    keywords = ["slowed", "reverb", "sped up", "pitched", "remix", "bootleg", "mashup", "cover"]
    return any(kw in lower for kw in keywords)


def _uploader_matches(uploader: str, artist: str) -> bool:
    """Проверяет совпадает ли uploader с артистом."""
    if not uploader or not artist:
        return False
    up = uploader.lower().strip()
    # Прямое совпадение
    if up == artist or artist in up or up in artist:
        return True
    # Убираем спецсимволы для сравнения
    import re
    clean_up = re.sub(r"[^a-zа-яё0-9]", "", up)
    clean_art = re.sub(r"[^a-zа-яё0-9]", "", artist)
    return clean_up == clean_art or clean_art in clean_up or clean_up in clean_art


def _closest_by_duration(tracks: List[Dict], expected: int, tolerance: int = 30) -> Optional[Dict]:
    """Находит трек ближайший по duration."""
    best = None
    best_diff = 999
    for t in tracks:
        if t["duration"] > 0:
            diff = abs(t["duration"] - expected)
            if diff < best_diff:
                best_diff = diff
                best = t
    if best and best_diff < tolerance:
        return best
    return None


def _download_track(url: str, dest_dir: str) -> Optional[str]:
    """Скачивает конкретный трек с SoundCloud по URL."""
    final_path = os.path.join(dest_dir, "sc_tmp.mp3")

    # Удаляем старый файл если есть
    if os.path.exists(final_path):
        try:
            os.remove(final_path)
        except OSError:
            pass

    cmd = [
        "yt-dlp",
        "--quiet",
        "--no-warnings",
        "--format", "bestaudio/best",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--paths", dest_dir,
        "--output", "sc_tmp.%(ext)s",
        url,
    ] + _get_ffmpeg_args()

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            log.warning("soundcloud_download_url_failed", url=url, stderr=result.stderr[:200])
            return None

        if os.path.exists(final_path):
            file_size = os.path.getsize(final_path)
            if file_size >= 1024:
                log.info("soundcloud_download_ok", url=url, size_mb=round(file_size / 1024 / 1024, 2))
                return final_path
            else:
                log.warning("soundcloud_file_too_small", url=url, size=file_size)
                try:
                    os.remove(final_path)
                except OSError:
                    pass

        # yt-dlp может сохранить с другим именем
        for f in os.listdir(dest_dir):
            if f.startswith("sc_tmp") and f.endswith(".mp3"):
                fp = os.path.join(dest_dir, f)
                if os.path.getsize(fp) >= 1024:
                    return fp

        return None

    except subprocess.TimeoutExpired:
        log.warning("soundcloud_download_timeout", url=url)
        return None
    except Exception as e:
        log.warning("soundcloud_download_error", url=url, error=str(e))
        return None


def search_and_download(artist: str, title: str, dest_dir: str, duration: int = 0) -> Optional[str]:
    """
    Ищет трек на SoundCloud и скачивает как MP3 через yt-dlp.

    Двухэтапный подход:
    1. scsearch5 — получаем 5 результатов с duration
    2. Выбираем лучший (ближайший по duration, не remix)
    3. Скачиваем конкретный URL

    Args:
        artist: Имя артиста (очищенное от feat./prod.)
        title: Название трека (очищенное от feat./prod.)
        dest_dir: Папка для сохранения
        duration: Ожидаемая длительность в секундах (для фильтрации)

    Returns:
        Путь к MP3 файлу или None
    """
    query = f"{artist} {title}"

    log.info("soundcloud_search", query=query, duration=duration)

    # Этап 1: Поиск
    tracks = _search_tracks(query, count=5)
    if not tracks:
        log.warning("soundcloud_no_results", query=query)
        return None

    log.info("soundcloud_search_results", query=query, count=len(tracks),
             titles=[t["title"][:40] for t in tracks],
             durations=[round(t["duration"]) for t in tracks])

    # Этап 2: Сортируем по приоритету (от артиста > не-ремикс > по duration)
    best = _pick_best(tracks, duration, artist)
    if not best:
        return None

    # Формируем список кандидатов: только не-ремиксы ( slowed/reverb → mp3party )
    ordered = [best]
    for t in tracks:
        if t["url"] != best["url"] and not _is_remix(t["title"]):
            ordered.append(t)

    # Этап 3: Пробуем скачать только не-ремиксы
    for i, track in enumerate(ordered[:3]):
        log.info("soundcloud_try_download", title=track["title"][:50],
                 uploader=track.get("uploader", "")[:30],
                 duration=round(track["duration"]), attempt=i + 1)
        result = _download_track(track["url"], dest_dir)
        if result:
            return result
        log.info("soundcloud_try_next", failed_url=track["url"][:60])

    log.info("soundcloud_all_failed_mp3party_fallback", query=query)
    return None
