"""Общие утилиты источников: модель кандидата, ранжирование, запуск yt-dlp."""

import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from rapidfuzz import fuzz

from utils.ffmpeg import get_ytdlp_ffmpeg_args
from utils.logger import get_logger

log = get_logger("source_common")


@dataclass
class Candidate:
    """Кандидат трека из источника (YouTube/SoundCloud/VK)."""
    url: str
    title: str
    artist: str = ""
    uploader: str = ""
    duration: float = 0.0
    thumbnail: str = ""
    source: str = ""
    cover_url: str = ""  # для VK — прямая ссылка на обложку


@dataclass
class DownloadResult:
    """Результат скачивания трека."""
    file_path: str
    cover_path: str = ""
    artist: str = ""
    title: str = ""
    duration: int = 0
    source_url: str = ""
    source: str = ""
    thumbnail_url: str = ""


_REMIX_KEYWORDS = ["slowed", "reverb", "sped up", "pitched", "bootleg", "mashup"]
_BRACKET_NOISE = re.compile(
    r"[\(\[]\s*(?:official|lyrics?|audio|video|visualizer|prod\.?|produced by|"
    r"hd|hq|4k|remaster(?:ed)?|explicit|censor)\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
_MULTI_SPACE = re.compile(r"\s+")


def is_remix(title: str) -> bool:
    """Является ли трек slowed/sped/bootleg/mashup-версией."""
    lower = (title or "").lower()
    return any(kw in lower for kw in _REMIX_KEYWORDS)


def uploader_matches(uploader: str, artist: str) -> bool:
    """Совпадает ли uploader/канал с ожидаемым артистом."""
    if not uploader or not artist:
        return False
    up = uploader.lower().strip()
    if up == artist or artist in up or up in artist:
        return True
    clean_up = re.sub(r"[^a-zа-яё0-9]", "", up)
    clean_art = re.sub(r"[^a-zа-яё0-9]", "", artist.lower())
    return bool(clean_up) and (clean_up == clean_art or clean_art in clean_up or clean_up in clean_art)


def normalize(text: str) -> str:
    """Нормализация строки для сравнения."""
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9 ]+", " ", text)
    return _MULTI_SPACE.sub(" ", text).strip()


def split_artist_title(title: str, uploader: str = "") -> tuple[str, str]:
    """Пытается вытащить артиста и название из сырого заголовка трека."""
    cleaned = _BRACKET_NOISE.sub("", title or "")
    cleaned = re.sub(r"\s*[\(\[]\s*[\)\]]\s*", " ", cleaned)
    cleaned = _MULTI_SPACE.sub(" ", cleaned).strip(" -–—")

    parts = re.split(r"\s+[-–—]\s+", cleaned, maxsplit=1)
    if len(parts) == 2:
        artist, track = parts[0].strip(), parts[1].strip()
        if artist and track:
            return artist, track

    return (uploader or "").strip(), cleaned


def score_candidate(c: Candidate, term: str, expected_duration: int, artist_hint: str) -> float:
    """Скоринг кандидата: ремикс, совпадение артиста, близость названия/длительности."""
    s = 0.0
    if not is_remix(c.title):
        s += 40

    if artist_hint and uploader_matches(c.uploader, artist_hint):
        s += 50
    if artist_hint and uploader_matches(c.artist, artist_hint):
        s += 30

    if term:
        s += fuzz.token_set_ratio(normalize(term), normalize(c.title)) * 0.6
        if c.artist:
            s += fuzz.token_set_ratio(normalize(term), normalize(f"{c.artist} {c.title}")) * 0.2

    if expected_duration > 0 and c.duration > 0:
        diff = abs(c.duration - expected_duration)
        if diff < 10:
            s += 40
        elif diff < 20:
            s += 25
        elif diff < 35:
            s += 10
        elif diff > 75:
            s -= 35

    return s


def rank_candidates(
    candidates: List[Candidate],
    term: str,
    expected_duration: int = 0,
    artist_hint: str = "",
    limit: int = 3,
) -> List[Candidate]:
    """Возвращает до `limit` лучших кандидатов по убыванию скоринга."""
    if not candidates:
        return []
    scored = sorted(
        candidates,
        key=lambda c: score_candidate(c, term, expected_duration, artist_hint),
        reverse=True,
    )
    return scored[:limit]


def run_ytdlp(args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Запускает yt-dlp с базовыми флагами."""
    cmd = ["yt-dlp", "--no-warnings", "--no-update"] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="ignore")


def parse_search_output(stdout: str, source: str) -> List[Candidate]:
    """Парсит вывод --print "<url>|||<title>|||<duration>|||<uploader>|||<channel>"."""
    results = []
    for line in (stdout or "").strip().splitlines():
        if "|||" not in line:
            continue
        parts = [p.strip() for p in line.split("|||")]
        if len(parts) < 4 or not parts[0]:
            continue
        url, title = parts[0], parts[1]
        try:
            duration = float(parts[2])
        except (ValueError, TypeError):
            duration = 0.0
        uploader = parts[3]
        channel = parts[4] if len(parts) > 4 else ""
        results.append(Candidate(
            url=url, title=title, uploader=uploader or channel,
            duration=duration, source=source,
        ))
    return results


def download_audio(
    url: str,
    dest_dir: str,
    out_stem: str,
    proxy: str = "",
    source: str = "",
    embed_thumb: bool = True,
) -> Optional[DownloadResult]:
    """Скачивает аудио в mp3 (yt-dlp). Поддерживает mp3-ссылки и HLS (VK).

    embed_thumb=False — не встраивать/не писать обложку (для VK, где своя обложка).
    """
    os.makedirs(dest_dir, exist_ok=True)
    out_tmpl = os.path.join(dest_dir, f"{out_stem}.%(ext)s")
    final_mp3 = os.path.join(dest_dir, f"{out_stem}.mp3")
    final_thumb = os.path.join(dest_dir, f"{out_stem}.jpg")

    for p in (final_mp3, final_thumb):
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    args = [
        "--no-warnings", "--no-update", "--force-overwrites",
        "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "--paths", dest_dir,
        "--output", out_tmpl,
        "--no-simulate",
        "--print", "META|||%(artist,uploader)s|||%(track,title)s|||%(duration)s|||%(thumbnail)s|||%(webpage_url)s",
    ]
    if embed_thumb:
        args += ["--embed-thumbnail", "--convert-thumbnails", "jpg", "--embed-metadata", "--write-thumbnail"]
    args += get_ytdlp_ffmpeg_args()
    if proxy:
        args += ["--proxy", proxy]
    args.append(url)

    try:
        result = run_ytdlp(args, timeout=180)
    except subprocess.TimeoutExpired:
        log.warning("download_timeout", url=url, source=source)
        return None
    except Exception as e:
        log.warning("download_error", url=url, source=source, error=str(e))
        return None

    if not os.path.exists(final_mp3) or os.path.getsize(final_mp3) < 1024:
        # ищем mp3 по имени стема
        found = None
        for f in os.listdir(dest_dir):
            if f.startswith(out_stem) and f.endswith(".mp3"):
                found = os.path.join(dest_dir, f)
                break
        if not found:
            log.warning("download_failed", url=url, source=source, stderr=(result.stderr or "")[:200])
            return None
        final_mp3 = found

    # метаданные
    artist = title = ""
    duration = 0
    thumb_url = ""
    source_url = url
    for line in (result.stdout or "").splitlines():
        if line.startswith("META|||"):
            parts = line.split("|||")
            if len(parts) >= 6:
                a = parts[1].strip()
                t = parts[2].strip()
                artist = "" if a in ("NA", "None") else a
                title = "" if t in ("NA", "None") else t
                try:
                    duration = int(float(parts[3]))
                except (ValueError, TypeError):
                    duration = 0
                thumb_url = "" if parts[4].strip() in ("NA", "None") else parts[4].strip()
                source_url = parts[5].strip() or url
            break

    # обложка: ищем файл с нашим стемом (jpg/png/webp)
    cover_path = final_thumb if os.path.exists(final_thumb) else ""
    if not cover_path:
        for f in os.listdir(dest_dir):
            if f.startswith(out_stem) and f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                cover_path = os.path.join(dest_dir, f)
                break

    return DownloadResult(
        file_path=final_mp3,
        cover_path=cover_path,
        artist=artist,
        title=title,
        duration=duration,
        source_url=source_url,
        source=source,
        thumbnail_url=thumb_url,
    )
