"""Оценки/достижения с risazatvorchestvo.com (РЗТ). Best-effort, с анти-ботом.

Сайт отдаёт данные только после 2-шагового запроса: сначала GET / (ставит cookie
_yasc), затем запрос с этой cookie. Всё обёрнуто в try/except — если не вышло,
просто возвращаем None и карточка альбома не ломается.
"""

import asyncio
import re
import time
from typing import Optional
from urllib.parse import quote_plus

import aiohttp
from rapidfuzz import fuzz

from utils.logger import get_logger

log = get_logger("rzt")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_BASE = "https://risazatvorchestvo.com"
_CACHE_TTL = 3600
_COOLDOWN = 900  # если РЗТ недоступен — не дёргаем 15 минут
_disabled_until = 0.0
_search_cache: dict[str, tuple[float, Optional[str]]] = {}
_rating_cache: dict[str, tuple[float, Optional[dict]]] = {}


async def _get(path: str, timeout: int = 5) -> Optional[str]:
    """2-шаговый GET (cookie _yasc) — HTML или None (в т.ч. во время «предохранителя»)."""
    global _disabled_until
    if time.time() < _disabled_until:
        return None
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
            async with session.get(
                _BASE + "/", timeout=aiohttp.ClientTimeout(total=timeout),
            ) as r:
                await r.read()
            async with session.get(
                _BASE + path, timeout=aiohttp.ClientTimeout(total=timeout),
            ) as r:
                if r.status != 200:
                    log.warning("rzt_bad_status", path=path, status=r.status)
                    _disabled_until = time.time() + _COOLDOWN
                    return None
                return await r.text()
    except Exception as e:
        _disabled_until = time.time() + _COOLDOWN
        log.warning("rzt_fetch_error", path=path, error=str(e))
        return None


def _slug_to_text(slug: str) -> str:
    return slug.replace("-", " ").strip()


def _pick_slug(query: str, slugs: list[str]) -> Optional[str]:
    """Выбирает наиболее похожий slug (fuzzy) на запрос."""
    if not slugs:
        return None
    best, best_score = None, 0.0
    for slug in slugs:
        score = fuzz.token_set_ratio(query.lower(), _slug_to_text(slug).lower())
        if score > best_score:
            best, best_score = slug, score
    return best if best_score >= 55 else None


async def search_album(query: str) -> Optional[str]:
    """Ищет slug альбома по строке «Артист Альбом»."""
    key = query.strip().lower()
    cached = _search_cache.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    html = await _get("/search?query=" + quote_plus(query))
    slug = None
    if html:
        slugs: list[str] = []
        for m in re.findall(r"/album/([a-z0-9][a-z0-9\-]+)", html):
            if m not in slugs:
                slugs.append(m)
        slug = _pick_slug(query, slugs)
    _search_cache[key] = (time.time(), slug)
    log.info("rzt_search_done", query=query[:40], slug=slug)
    return slug


def _parse_host_score(html: str) -> Optional[int]:
    """Оценка ведущего РЗТ — блок avg/count, где count == 0."""
    for m in re.finditer(r'\\?"avg\\?":(\d+),\\?"count\\?":(\d+)', html):
        if int(m.group(2)) == 0:
            return int(m.group(1))
    return None


def _parse_tier(html: str) -> Optional[tuple[str, str]]:
    """Бейдж «<Тир> альбом» + значение, напр. ('Сапфировый альбом', '19.13')."""
    m = re.search(
        r">([А-ЯЁ][^<>]{0,30}альбом)</div><div[^>]*>\s*([0-9]+[.,][0-9]+)\s*</div>",
        html,
    )
    if not m:
        return None
    return m.group(1).strip(), m.group(2).replace(",", ".")


def _parse_nominations(html: str) -> list[str]:
    """Best-effort список номинаций релиза."""
    out: list[str] = []

    # 1) JSON-массивы рядом с ключом nomin*/award*
    for m in re.finditer(r'\\?"(?:nominations|awards|award_nominations)\\?":\s*\[(.*?)\]', html, re.DOTALL):
        arr = m.group(1)
        for t in re.findall(r'\\?"(?:title|name|award|nomination|label)\\?":\s*\\?"([^"\\]{2,80})\\"', arr):
            out.append(t.strip())

    # 2) видимые названия премий/номинаций рядом со словом «номинация»
    for m in re.finditer(r"(?s).{0,120}номинаци.{0,200}", html):
        for t in re.findall(r'children\\?":\\?"([^"\\]{2,80})\\"', m.group(0)):
            out.append(t.strip())

    seen: set[str] = set()
    result: list[str] = []
    for x in out:
        x = re.sub(r"\s+", " ", x).strip()
        low = x.lower()
        if x and low not in seen and "номинаци" not in low:
            seen.add(low)
            result.append(x)
    return result[:12]


def _parse_rating(html: Optional[str]) -> Optional[dict]:
    if not html:
        return None
    rzt = _parse_host_score(html)
    tier = _parse_tier(html)
    nominations = _parse_nominations(html)
    if rzt is None and tier is None and not nominations:
        return None
    return {"rzt": rzt, "tier": tier, "nominations": nominations}


async def get_rating(slug: str) -> Optional[dict]:
    """Оценка/тир/номинации по slug альбома."""
    cached = _rating_cache.get(slug)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    data = _parse_rating(await _get(f"/album/{slug}"))
    _rating_cache[slug] = (time.time(), data)
    log.info("rzt_rating_done", slug=slug, rzt=(data or {}).get("rzt"), tier=bool((data or {}).get("tier")))
    return data


async def _rating_pipeline(artist: str, title: str) -> Optional[dict]:
    slug = await search_album(f"{artist} {title}")
    if not slug:
        return None
    return await get_rating(slug)


async def get_rating_for(artist: str, title: str) -> Optional[dict]:
    """Полный путь: поиск альбома → оценка. None, если не нашлось/заблокировано."""
    if not artist or not title:
        return None
    try:
        return await asyncio.wait_for(_rating_pipeline(artist, title), timeout=8)
    except Exception as e:
        log.warning("rzt_rating_timeout", artist=artist[:40], title=title[:40], error=str(e))
        return None
