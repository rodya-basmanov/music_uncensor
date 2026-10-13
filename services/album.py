"""Поиск и метаданные альбомов (Deezer).

Публичное:
    search_albums(query) -> list[AlbumInfo]
    get_album(source, id) -> AlbumInfo | None
    pretty_artist(name) -> str   (best-effort регистр написания, через VK)
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote_plus

import aiohttp
from aiohttp_socks import ProxyConnector
from rapidfuzz import fuzz

from utils.helpers import Track, translit
from utils.logger import get_logger

log = get_logger("album")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_CACHE_TTL = 600
_search_cache: dict[str, tuple[float, list]] = {}
_album_cache: dict[tuple[str, str], tuple[float, "AlbumInfo"]] = {}


@dataclass
class AlbumInfo:
    """Метаданные альбома и список треков."""

    source: str
    id: str
    artist: str
    title: str
    year: str = ""
    release_date: str = ""
    genre: str = ""
    label: str = ""
    contributors: list[str] = field(default_factory=list)
    cover_url: str = ""
    total_duration: int = 0
    tracks: list[Track] = field(default_factory=list)

    @property
    def label_str(self) -> str:
        parts = []
        if self.artist:
            parts.append(self.artist)
        if self.year:
            parts.append(self.year)
        if self.genre:
            parts.append(self.genre)
        return " · ".join(parts)


_sessions: dict[str, aiohttp.ClientSession] = {}


def _session(proxy: str) -> aiohttp.ClientSession:
    """Переиспользуемая aiohttp-сессия для прокси/без него."""
    s = _sessions.get(proxy)
    if s is None or s.closed:
        connector = ProxyConnector.from_url(proxy) if proxy else None
        s = aiohttp.ClientSession(connector=connector)
        _sessions[proxy] = s
    return s


async def _get_json(url: str, proxy: str = "", timeout: int = 8) -> Optional[Any]:
    session = _session(proxy)
    for attempt in range(2):
        try:
            async with session.get(
                url, headers={"User-Agent": _UA}, timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json(content_type=None)
        except Exception as e:
            if attempt == 1:
                log.warning("album_http_error", url=url[:80], error=str(e))
    return None


async def _fetch(url: str, proxy: str = "") -> Optional[Any]:
    """Сначала напрямую, при неудаче — через прокси (если задан)."""
    data = await _get_json(url, "")
    if data is None and proxy:
        data = await _get_json(url, proxy)
    return data


# ----------------------------- Deezer ----------------------------------------

def parse_deezer_search(data: Any) -> list[AlbumInfo]:
    out: list[AlbumInfo] = []
    for r in (data or {}).get("data", []) or []:
        if not r.get("id"):
            continue
        out.append(AlbumInfo(
            source="deezer",
            id=str(r.get("id")),
            artist=(r.get("artist") or {}).get("name", ""),
            title=r.get("title", ""),
            cover_url=r.get("cover_xl") or r.get("cover_big") or r.get("cover_medium") or "",
        ))
    return out


def parse_deezer_album(data: Any) -> Optional[AlbumInfo]:
    if not data or data.get("error") or not data.get("id"):
        return None
    artist = (data.get("artist") or {}).get("name", "")
    tracks: list[Track] = []
    for t in (data.get("tracks") or {}).get("data", []) or []:
        title = (t.get("title") or "").strip()
        if not title:
            continue
        tracks.append(Track(
            artist=artist, title=title, duration=int(t.get("duration") or 0),
        ))
    genres = [g.get("name", "") for g in (data.get("genres") or {}).get("data", []) or []]
    contributors: list[str] = []
    for c in data.get("contributors", []) or []:
        name = (c.get("name") or "").strip()
        role = (c.get("role") or "").strip()
        if not name or name.lower() == artist.lower():
            continue
        contributors.append(f"{name} ({role})" if role else name)
    return AlbumInfo(
        source="deezer",
        id=str(data.get("id")),
        artist=artist,
        title=data.get("title", ""),
        year=(data.get("release_date") or "")[:4],
        release_date=data.get("release_date", "") or "",
        genre=", ".join(genres),
        label=data.get("label", ""),
        contributors=contributors,
        cover_url=data.get("cover_xl") or data.get("cover_big") or "",
        total_duration=int(data.get("duration") or 0),
        tracks=tracks,
    )


# ----------------------------- регистр артиста --------------------------------

def _pick_casing(name: str, candidates: list[str]) -> str:
    """Выбирает наиболее «стилизованное» написание среди совпадений."""
    target = name.strip().lower()
    matches = [c.strip() for c in candidates if c and c.strip().lower() == target]
    if not matches:
        return name

    def score(s: str) -> int:
        return sum(1 for ch in s if ch.isupper())

    best = max(matches, key=lambda s: (score(s), matches.count(s)))
    return best


async def pretty_artist(name: str, proxy: str = "", vk_fetcher=None) -> str:
    """Best-effort восстановление написания имени артиста (напр. villian → VILLIAN).

    Использует только VK (без iTunes), с таймаутом — Deezer отдаёт имя строчными.
    """
    if not name or vk_fetcher is None:
        return name
    if any(ch.isupper() for ch in name):
        return name  # регистр уже нормальный — не дёргаем VK
    candidates: list[str] = []
    try:
        res = await asyncio.wait_for(
            asyncio.to_thread(vk_fetcher.search, name, 8), timeout=8,
        )
        candidates += [getattr(c, "artist", "") for c in res]
    except Exception:
        pass
    picked = _pick_casing(name, candidates)
    if picked != name:
        log.info("artist_casing_fixed", original=name, picked=picked)
    return picked


# ----------------------------- публичное -------------------------------------

def _query_variants(query: str) -> list[str]:
    """Варианты для Deezer: как есть, транслит целиком, слово-артист, остаток.

    Deezer по слову-артисту (`saluki`) отдаёт все его альбомы, тогда как по всей
    фразе `салуки эйфория` может вернуть 0.
    """
    q = query.strip()
    variants: list[str] = []

    def add(s: str) -> None:
        s = s.strip()
        if s and s.lower() not in {v.lower() for v in variants}:
            variants.append(s)

    add(q)
    add(translit(q))
    words = [w for w in q.split() if w]
    if len(words) > 1:
        add(translit(words[0]))
        add(translit(" ".join(words[1:])))
    elif words:
        add(translit(words[0]))
    return variants[:4]


def _rank_albums(query: str, albums: list[AlbumInfo]) -> list[AlbumInfo]:
    """Сортирует альбомы по похожести на запрос (с учётом транслита)."""
    q = translit(query).lower()
    if not q:
        return albums

    def score(a: AlbumInfo) -> float:
        hay = translit(f"{a.artist} {a.title}").lower()
        return fuzz.token_set_ratio(q, hay)

    return sorted(albums, key=score, reverse=True)


async def search_albums(query: str, proxy: str = "", limit: int = 5) -> list[AlbumInfo]:
    """Ищет альбомы в Deezer по всем вариантам запроса (параллельно), ранжирует."""
    key = query.strip().lower()
    cached = _search_cache.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    per = max(limit, 10)
    urls = [
        "https://api.deezer.com/search/album?q=" + quote_plus(v) + f"&limit={per}"
        for v in _query_variants(query)
    ]
    groups = await asyncio.gather(*(_fetch(u, proxy) for u in urls))

    pool: list[AlbumInfo] = []
    seen: set = set()
    for data in groups:
        for a in parse_deezer_search(data):
            ck = (a.source, a.id)
            if ck not in seen:
                seen.add(ck)
                pool.append(a)

    albums = _rank_albums(query, pool)[:limit] if pool else []
    _search_cache[key] = (time.time(), albums)
    log.info("album_search_done", query=query[:40], count=len(albums))
    return albums


async def get_album(source: str, album_id: str, proxy: str = "") -> Optional[AlbumInfo]:
    """Загружает детали альбома Deezer (треки, год, жанр, лейбл, обложка)."""
    ck = (source, str(album_id))
    cached = _album_cache.get(ck)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    album: Optional[AlbumInfo] = None
    if source == "deezer":
        album = parse_deezer_album(await _fetch(f"https://api.deezer.com/album/{album_id}", proxy))

    if album is not None:
        _album_cache[ck] = (time.time(), album)
    return album
