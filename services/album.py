"""Поиск и метаданные альбомов (Deezer).

Публичное:
    search_albums(query) -> list[AlbumInfo]
    get_album(source, id) -> AlbumInfo | None
    pretty_artist(name) -> str   (best-effort регистр написания, через VK)
"""

import asyncio
import dataclasses
import re
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

_db = None  # utils.db.Database — персистентный кэш поиска (переживает рестарт)


def set_db(db) -> None:
    """Подключает SQLite для персистентного кэша поиска альбомов/артистов."""
    global _db
    _db = db


def _album_to_dict(a: "AlbumInfo") -> dict:
    return dataclasses.asdict(a)


def _album_from_dict(d: dict) -> "AlbumInfo":
    data = dict(d)
    tracks = [Track(**t) for t in data.pop("tracks", []) or []]
    return AlbumInfo(tracks=tracks, **data)


def _albums_to_dicts(albums: list) -> list:
    return [_album_to_dict(a) for a in albums]


def _albums_from_dicts(items: list) -> list:
    out = []
    for d in items or []:
        try:
            out.append(_album_from_dict(d))
        except Exception:
            continue
    return out


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


async def _get_json(url: str, proxy: str = "", timeout: int = 4) -> Optional[Any]:
    session = _session(proxy)
    for attempt in range(2):
        try:
            async with session.get(
                url, headers={"User-Agent": _UA}, timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                if isinstance(data, dict) and data.get("error"):
                    err = data["error"]
                    code = err.get("code") if isinstance(err, dict) else ""
                    log.warning("deezer_error", url=url[:60], code=code)
                    await asyncio.sleep(0.3)
                    continue
                return data
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


def _norm_title(s: str) -> str:
    """Нормализация названия трека для сопоставления Deezer↔VK."""
    s = translit(s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _split_artists(artist_str: str) -> tuple[str, list[str]]:
    """'SALUKI, Sabu' -> ('SALUKI', ['Sabu'])."""
    parts = [p.strip() for p in re.split(r"\s*[,&/]\s*|\s+(?:feat|ft)\.?\s+", artist_str or "") if p.strip()]
    if not parts:
        return "", []
    return parts[0], parts[1:]


async def enrich_with_vk(album: AlbumInfo, vk_fetcher) -> int:
    """Находит VK-альбом по пересечению треков и пришивает url/feat к трекам Deezer.

    Возвращает число совпавших треков. Best-effort.
    """
    if vk_fetcher is None or not album.tracks:
        return 0
    query = f"{album.artist} {album.title}".strip()
    try:
        groups = await asyncio.to_thread(vk_fetcher.search_albums, query, 100)
    except Exception as e:
        log.warning("vk_album_search_error", error=str(e))
        return 0
    if not groups:
        return 0

    deezer_norms = {_norm_title(t.title) for t in album.tracks if t.title}
    if not deezer_norms:
        return 0
    # Порог подтверждения: ~third треклиста (для коротких альбомов — послабже,
    # иначе даже верный VK-альбом из 3-6 треков не проходит).
    need = max(1, len(deezer_norms) // 3)

    def window_overlap(g: dict) -> int:
        return len({_norm_title(t["title"]) for t in g["tracks"]} & deezer_norms)

    ordered = sorted(groups, key=lambda g: (window_overlap(g), len(g["tracks"])), reverse=True)

    vk_tracks: list[Track] = []
    best_tracks: list[Track] = []
    best_overlap = -1
    for g in ordered[:3]:
        try:
            cand = await asyncio.to_thread(
                vk_fetcher.get_playlist_tracks, g["owner_id"], g["album_id"], g["access_key"], 200,
            )
        except Exception as e:
            log.warning("vk_album_load_error", error=str(e))
            continue
        if not cand:
            continue
        # подтверждаем совпадение по полному треклисту, запоминая лучшего кандидата
        ov = len({_norm_title(v.title) for v in cand} & deezer_norms)
        if ov > best_overlap:
            best_overlap = ov
            best_tracks = cand
        if ov >= len(deezer_norms):
            break
    if best_tracks and best_overlap >= need:
        vk_tracks = best_tracks
    if not vk_tracks:
        return 0

    by_title: dict[str, Track] = {}
    for v in vk_tracks:
        by_title.setdefault(_norm_title(v.title), v)

    main_artist = (album.artist or "").strip().lower()
    matched = 0
    for t in album.tracks:
        vk_track = by_title.get(_norm_title(t.title))
        if not vk_track:
            continue
        _, feats = _split_artists(vk_track.artist)
        feats = [f for f in feats if f.lower() != main_artist]
        if feats:
            t.feat = feats
        if vk_track.url:
            t.url = vk_track.url
            t.source = "vk"
        if not t.cover_url and vk_track.cover_url:
            t.cover_url = vk_track.cover_url
        matched += 1
    if matched:
        log.info("vk_album_enriched", album=album.title[:40], matched=matched, total=len(album.tracks))
    return matched


async def search_albums_vk(query: str, vk_fetcher) -> list[AlbumInfo]:
    """Ищет альбомы в VK (группировка по album.id). Best-effort."""
    if vk_fetcher is None:
        return []
    try:
        groups = await asyncio.to_thread(vk_fetcher.search_albums, query, 100)
    except Exception as e:
        log.warning("vk_album_search_error", error=str(e))
        return []
    out: list[AlbumInfo] = []
    for g in groups[:5]:
        if not g.get("tracks"):
            continue
        artist = (g.get("artists") or [query])[0]
        title = g.get("title") or ""
        if not title or title.lower() == "unnamed":
            rem = re.sub(re.escape(artist), "", query, flags=re.IGNORECASE).strip(" -—")
            title = rem or query
        out.append(AlbumInfo(
            source="vk",
            id=f"{g['owner_id']}_{g['album_id']}_{g['access_key']}",
            artist=artist,
            title=title,
            cover_url=g.get("cover_url") or "",
        ))
    return out


async def get_album_vk(alb: AlbumInfo, vk_fetcher) -> Optional[AlbumInfo]:
    """Загружает VK-альбом целиком (audio.get) по id 'owner_album_access'."""
    if vk_fetcher is None:
        return None
    try:
        owner, aid, key = str(alb.id).split("_", 2)
    except ValueError:
        return None
    try:
        raw = await asyncio.to_thread(vk_fetcher.get_playlist_tracks, int(owner), int(aid), key, 200)
    except Exception as e:
        log.warning("vk_album_load_error", error=str(e))
        return None
    if not raw:
        return None
    main_artist = (alb.artist or "").strip().lower()
    tracks: list[Track] = []
    for v in raw:
        _, feats = _split_artists(v.artist)
        feats = [f for f in feats if f.lower() != main_artist]
        tracks.append(Track(
            artist=alb.artist or v.artist, title=v.title, duration=v.duration,
            url=v.url, cover_url=v.cover_url or alb.cover_url, source="vk", feat=feats,
        ))
    return AlbumInfo(
        source="vk", id=alb.id, artist=alb.artist, title=alb.title,
        cover_url=alb.cover_url or (tracks[0].cover_url if tracks else ""),
        tracks=tracks,
        total_duration=sum(t.duration for t in tracks if t.duration > 0),
    )


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
            source_id=str(t.get("id") or ""),
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


def parse_deezer_artist(data: Any) -> list[dict]:
    """Разбирает ответ /search/artist в [{id, name, fans}]."""
    out: list[dict] = []
    for r in (data or {}).get("data", []) or []:
        name = (r.get("name") or "").strip()
        if r.get("id") and name:
            out.append({"id": str(r["id"]), "name": name, "fans": int(r.get("nb_fan") or 0)})
    return out


def parse_deezer_artist_albums(data: Any, artist_name: str = "") -> list[AlbumInfo]:
    """Разбирает ответ /artist/{id}/albums в список AlbumInfo (без треков).

    В ответе Deezer у релизов нет поля ``artist`` — подставляем имя искомого артиста.
    """
    out: list[AlbumInfo] = []
    for r in (data or {}).get("data", []) or []:
        if not r.get("id"):
            continue
        out.append(AlbumInfo(
            source="deezer",
            id=str(r.get("id")),
            artist=(r.get("artist") or {}).get("name") or artist_name,
            title=(r.get("title") or "").strip(),
            year=(r.get("release_date") or "")[:4],
            release_date=r.get("release_date", "") or "",
            cover_url=r.get("cover_xl") or r.get("cover_big") or r.get("cover_medium") or "",
        ))
    return out


async def search_artist(name: str, proxy: str = "") -> Optional[dict]:
    """Ищет артиста в Deezer. Возвращает {'id','name'} при уверенном совпадении.

    У Deezer бывает несколько артистов с одинаковым именем — среди совпавших
    по имени выбираем самого популярного (``nb_fan``).
    """
    q = (name or "").strip()
    if not q:
        return None
    target = _norm_title(q)
    if not target:
        return None
    url = "https://api.deezer.com/search/artist?q=" + quote_plus(q) + "&limit=10"
    data = None
    for attempt in range(2):  # первый запрос бывает «холодным» — не сдаёмся сразу
        try:
            data = await asyncio.wait_for(_fetch(url, proxy), timeout=8)
        except Exception:
            data = None
        if isinstance(data, dict):
            break
        if attempt == 0:
            await asyncio.sleep(0.4)
    if not isinstance(data, dict):
        return None
    cands = parse_deezer_artist(data)
    exact = [a for a in cands if _norm_title(a["name"]) == target]
    if exact:
        pool = exact
    else:
        pool = [a for a in cands if fuzz.token_set_ratio(target, _norm_title(a["name"])) >= 92]
    if not pool:
        return None
    best = max(pool, key=lambda a: a.get("fans", 0))
    return {"id": best["id"], "name": best["name"]}


async def search_artist_albums(name: str, proxy: str = "", limit: int = 50) -> list[AlbumInfo]:
    """Все альбомы артиста (Deezer), свежие сверху. Пусто, если артист не найден."""
    key = "artist:" + (name or "").strip().lower()
    cached = _search_cache.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    if _db is not None:
        try:
            stored = _db.cache_get(key)
            if stored is not None and stored:
                albums = _albums_from_dicts(stored)
                if albums:
                    _search_cache[key] = (time.time(), albums)
                    return albums
        except Exception:
            pass
    artist = await search_artist(name, proxy)
    if not artist:
        # не кэшируем «пусто»: иначе разовый сетевой сбой выключит фичу на 10 минут
        return []
    n = max(1, min(int(limit), 100))
    url = f"https://api.deezer.com/artist/{artist['id']}/albums?limit={n}"
    try:
        data = await asyncio.wait_for(_fetch(url, proxy), timeout=8)
    except Exception:
        data = None
    albums = parse_deezer_artist_albums(data, artist["name"]) if isinstance(data, dict) else []
    albums.sort(key=lambda a: (a.release_date or a.year or ""), reverse=True)
    _search_cache[key] = (time.time(), albums)
    if albums and _db is not None:
        try:
            _db.cache_set(key, _albums_to_dicts(albums), _CACHE_TTL)
        except Exception:
            pass
    log.info("artist_albums_done", artist=artist["name"], count=len(albums))
    return albums


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
    """Варианты для Deezer: как есть, транслит целиком, слово-артист, остаток."""
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


def _album_score(query: str, album: AlbumInfo) -> float:
    """Похожесть альбома на запрос (с учётом транслита), 0-100."""
    q = translit(query).lower()
    if not q:
        return 0.0
    hay = translit(f"{album.artist} {album.title}").lower()
    return fuzz.token_set_ratio(q, hay)


def _rank_albums(query: str, albums: list[AlbumInfo]) -> list[AlbumInfo]:
    """Сортирует альбомы по похожести на запрос (с учётом транслита)."""
    return sorted(albums, key=lambda a: _album_score(query, a), reverse=True)


async def search_albums(query: str, proxy: str = "", limit: int = 5) -> list[AlbumInfo]:
    """Ищет альбомы в Deezer по вариантам запроса (параллельно, с ранним выходом)."""
    key = query.strip().lower()
    cached = _search_cache.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    if _db is not None:
        try:
            stored = _db.cache_get("alb:" + key)
            if stored is not None:
                albums = _albums_from_dicts(stored)
                _search_cache[key] = (time.time(), albums)
                return albums
        except Exception:
            pass

    per = max(limit, 10)
    urls = [
        "https://api.deezer.com/search/album?q=" + quote_plus(v) + f"&limit={per}"
        for v in _query_variants(query)
    ]
    tasks = [
        asyncio.ensure_future(asyncio.wait_for(_fetch(u, proxy), timeout=8))
        for u in urls
    ]
    pool: list[AlbumInfo] = []
    seen: set = set()
    try:
        for fut in asyncio.as_completed(tasks):
            try:
                data = await fut
            except Exception:
                continue
            if isinstance(data, dict):
                for a in parse_deezer_search(data):
                    ck = (a.source, a.id)
                    if ck not in seen:
                        seen.add(ck)
                        pool.append(a)
            # уверенное совпадение — не ждём медленные варианты
            if pool and _album_score(query, _rank_albums(query, pool)[0]) >= 90:
                break
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    albums = _rank_albums(query, pool)[:limit] if pool else []
    # пустой результат не кэшируем: разовый сбой Deezer не должен «гасить» запрос на 10 мин
    if albums:
        _search_cache[key] = (time.time(), albums)
    if albums and _db is not None:
        try:
            _db.cache_set("alb:" + key, _albums_to_dicts(albums), _CACHE_TTL)
        except Exception:
            pass
    log.info("album_search_done", query=query[:40], count=len(albums))
    return albums


async def get_album(source: str, album_id: str, proxy: str = "") -> Optional[AlbumInfo]:
    """Загружает детали альбома Deezer (треки, год, жанр, лейбл, обложка)."""
    ck = (source, str(album_id))
    cached = _album_cache.get(ck)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    db_key = f"ali:{source}:{album_id}"
    if _db is not None:
        try:
            stored = _db.cache_get(db_key)
            if stored:
                cached_album = _album_from_dict(stored)
                _album_cache[ck] = (time.time(), cached_album)
                return cached_album
        except Exception:
            pass

    album: Optional[AlbumInfo] = None
    if source == "deezer":
        album = parse_deezer_album(await _fetch(f"https://api.deezer.com/album/{album_id}", proxy))

    if album is not None:
        _album_cache[ck] = (time.time(), album)
        if _db is not None:
            try:
                _db.cache_set(db_key, _album_to_dict(album), _CACHE_TTL)
            except Exception:
                pass
    return album
