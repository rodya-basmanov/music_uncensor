"""Поиск и метаданные альбомов (Deezer → iTunes fallback).

Публичное:
    search_albums(query) -> list[AlbumInfo]
    get_album(source, id) -> AlbumInfo | None
    pretty_artist(name) -> str   (best-effort регистр написания)
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote_plus

import aiohttp
from aiohttp_socks import ProxyConnector

from utils.helpers import Track
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


async def _get_json(url: str, proxy: str = "", timeout: int = 12) -> Optional[Any]:
    connector = ProxyConnector.from_url(proxy) if proxy else None
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                url, headers={"User-Agent": _UA}, timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json(content_type=None)
    except Exception as e:
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
        genre=", ".join(genres),
        label=data.get("label", ""),
        contributors=contributors,
        cover_url=data.get("cover_xl") or data.get("cover_big") or "",
        total_duration=int(data.get("duration") or 0),
        tracks=tracks,
    )


# ----------------------------- iTunes ----------------------------------------

def _itunes_cover(url: str) -> str:
    return (url or "").replace("100x100", "600x600").replace("60x60", "600x600")


def parse_itunes_search(data: Any) -> list[AlbumInfo]:
    out: list[AlbumInfo] = []
    for r in (data or {}).get("results", []) or []:
        if not r.get("collectionId"):
            continue
        out.append(AlbumInfo(
            source="itunes",
            id=str(r.get("collectionId")),
            artist=r.get("artistName", ""),
            title=r.get("collectionName", ""),
            year=(r.get("releaseDate") or "")[:4],
            genre=r.get("primaryGenreName", ""),
            cover_url=_itunes_cover(r.get("artworkUrl100", "")),
        ))
    return out


def parse_itunes_album(data: Any) -> Optional[AlbumInfo]:
    results = (data or {}).get("results", []) or []
    collection = next((r for r in results if r.get("wrapperType") == "collection"), None)
    if not collection:
        return None
    artist = collection.get("artistName", "")
    tracks: list[Track] = []
    for r in results:
        if r.get("wrapperType") != "track":
            continue
        title = (r.get("trackName") or "").strip()
        if not title:
            continue
        tracks.append(Track(
            artist=r.get("artistName", artist),
            title=title,
            duration=int((r.get("trackTimeMillis") or 0) // 1000),
        ))
    return AlbumInfo(
        source="itunes",
        id=str(collection.get("collectionId")),
        artist=artist,
        title=collection.get("collectionName", ""),
        year=(collection.get("releaseDate") or "")[:4],
        genre=collection.get("primaryGenreName", ""),
        cover_url=_itunes_cover(collection.get("artworkUrl100", "")),
        total_duration=sum(t.duration for t in tracks),
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
    """Best-effort восстановление написания имени артиста (напр. villian → VILLIAN)."""
    if not name:
        return name
    candidates: list[str] = []
    try:
        url = "https://itunes.apple.com/search?term=" + quote_plus(name) + "&entity=musicArtist&limit=8"
        data = await _fetch(url, proxy)
        candidates += [r.get("artistName", "") for r in (data or {}).get("results", []) or []]
    except Exception:
        pass
    if vk_fetcher is not None:
        try:
            res = await asyncio.to_thread(vk_fetcher.search, name, 8)
            candidates += [getattr(c, "artist", "") for c in res]
        except Exception:
            pass
    picked = _pick_casing(name, candidates)
    if picked != name:
        log.info("artist_casing_fixed", original=name, picked=picked)
    return picked


# ----------------------------- публичное -------------------------------------

async def search_albums(query: str, proxy: str = "", limit: int = 5) -> list[AlbumInfo]:
    """Ищет альбомы: Deezer, при пустом результате — iTunes."""
    key = query.strip().lower()
    cached = _search_cache.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    url = (
        "https://api.deezer.com/search/album?q="
        + quote_plus(query) + f"&limit={limit}"
    )
    albums = parse_deezer_search(await _fetch(url, proxy))
    if not albums:
        url = (
            "https://itunes.apple.com/search?term="
            + quote_plus(query) + f"&media=music&entity=album&limit={limit}"
        )
        albums = parse_itunes_search(await _fetch(url, proxy))

    _search_cache[key] = (time.time(), albums)
    log.info("album_search_done", query=query[:40], count=len(albums))
    return albums


async def get_album(source: str, album_id: str, proxy: str = "") -> Optional[AlbumInfo]:
    """Загружает детали альбома (треки, год, жанр, лейбл, обложка)."""
    ck = (source, str(album_id))
    cached = _album_cache.get(ck)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    album: Optional[AlbumInfo] = None
    if source == "deezer":
        album = parse_deezer_album(await _fetch(f"https://api.deezer.com/album/{album_id}", proxy))
    elif source == "itunes":
        url = f"https://itunes.apple.com/lookup?id={album_id}&entity=song"
        album = parse_itunes_album(await _fetch(url, proxy))

    if album is not None:
        _album_cache[ck] = (time.time(), album)
    return album
