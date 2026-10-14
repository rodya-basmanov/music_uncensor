"""Оценки/достижения с risazatvorchestvo.com (РЗТ). Best-effort, с анти-ботом.

Сайт закрыт Yandex SmartCaptcha: обычные HTTP-клиенты получают 400. Работает
только клиент с TLS-отпечатком браузера — используем curl_cffi (impersonate chrome).
Данные: сначала GET / (ставит cookie _yasc), затем страница. Всё обёрнуто в
try/except — если не вышло, возвращаем None и карточка альбома не ломается.
"""

import asyncio
import re
import time
from typing import Any, Optional
from urllib.parse import quote_plus

from curl_cffi.requests import AsyncSession
from rapidfuzz import fuzz

from utils.helpers import translit
from utils.logger import get_logger

log = get_logger("rzt")

_BASE = "https://risazatvorchestvo.com"
_CACHE_TTL = 3600
_COOLDOWN = 600  # выключаем РЗТ только при сетевых ошибках/5xx, на 10 минут
_STUB_COOLDOWN = 300  # заглушка анти-бота — пауза 5 минут
_disabled_until = 0.0
# сайт ротирует, какой TLS-отпечаток челленджит — перебираем при заглушке
_IMPERSONATIONS: list[Any] = ["chrome", "chrome120", "chrome119", "chrome123", "chrome116", "edge101", "chrome124"]
_imp_idx = 0
_session: Optional[AsyncSession] = None
_warmed = False
_search_cache: dict[str, tuple[float, Optional[str]]] = {}
_rating_cache: dict[str, tuple[float, Optional[dict]]] = {}


def _client() -> AsyncSession:
    """Сессия с текущим отпечатком (cookie _yasc сохраняется между запросами)."""
    global _session
    if _session is None:
        _session = AsyncSession(impersonate=_IMPERSONATIONS[_imp_idx])
    return _session


async def _reset_session() -> None:
    """Закрывает сессию и сбрасывает прогрев (для смены отпечатка)."""
    global _session, _warmed
    if _session is not None:
        try:
            await _session.close()
        except Exception:
            pass
    _session = None
    _warmed = False


async def close() -> None:
    """Закрывает общую сессию (при остановке бота)."""
    await _reset_session()


async def _get(path: str, timeout: int = 12) -> Optional[str]:
    """GET страницы РЗТ. При заглушке/сбое перебираем TLS-отпечатки. None при неудаче."""
    global _disabled_until, _warmed, _imp_idx
    if time.time() < _disabled_until:
        return None
    last_err = ""
    for _ in range(len(_IMPERSONATIONS)):
        try:
            session = _client()
            if not _warmed:
                await session.get(_BASE + "/", timeout=timeout)
                _warmed = True
            r = await session.get(_BASE + path, timeout=timeout)
            if r.status_code == 200 and "__next_f" in r.text and len(r.text) >= 100_000:
                return r.text
            last_err = "stub" if r.status_code == 200 else f"HTTP {r.status_code}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        # сменить отпечаток и попробовать снова
        _imp_idx = (_imp_idx + 1) % len(_IMPERSONATIONS)
        await _reset_session()
        await asyncio.sleep(0.4)

    if last_err == "stub" or not last_err:
        _disabled_until = time.time() + _STUB_COOLDOWN
        log.warning("rzt_stub", path=path)
    elif last_err.startswith("HTTP"):
        log.warning("rzt_bad_status", path=path, error=last_err)
    else:
        _disabled_until = time.time() + _COOLDOWN
        log.warning("rzt_fetch_error", path=path, error=last_err)
    return None


def _clean_query(query: str) -> str:
    """Убирает символы, ломающие поиск РЗТ (напр. '$' в 'WILD EA$T')."""
    cleaned = re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _slug_to_text(slug: str) -> str:
    return slug.replace("-", " ").strip()


def _pick_slug(query: str, slugs: list[str]) -> Optional[str]:
    """Выбирает наиболее похожий slug (fuzzy, с транслитом запроса и slug)."""
    if not slugs:
        return None
    q = translit(query).lower()
    best, best_score = None, 0.0
    for slug in slugs:
        score = fuzz.token_set_ratio(q, translit(_slug_to_text(slug)).lower())
        if score > best_score:
            best, best_score = slug, score
    return best if best_score >= 55 else None


async def search_album(query: str) -> Optional[str]:
    """Ищет slug альбома по строке «Артист Альбом»."""
    key = query.strip().lower()
    cached = _search_cache.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    html = await _get("/search?query=" + quote_plus(_clean_query(query) or query))
    if html is None:
        # заглушка/сеть — не кэшируем, попробуем в следующий раз
        log.info("rzt_search_skip", query=query[:40])
        return None
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


_NOM_CONTAINER_RE = re.compile(
    r'transition-colors (winner-surface bg-zinc-950|bg-white/5 hover:bg-white/10)'
)
_NOM_TITLE_RE = re.compile(
    r'truncate text-sm leading-tight font-(?:medium|semibold)\\?",\\?"children\\?":\\?"([^"\\]+)\\?"'
)
_NOM_PERIOD_RE = re.compile(
    r'mt-0\.5 text-xs leading-tight font-medium\\?",\\?"children\\?":\\?"([^"\\]+)\\?"'
)
_NOM_RESULT_RE = re.compile(
    r'ml-auto shrink-0 text-xs font-semibold text-zinc-100\\?",\\?"children\\?":\\?"([^"\\]+)\\?"'
)


def _parse_nominations(html: str) -> list[dict]:
    """Номинации релиза: [{title, period, result, winner}] из RSC-разметки."""
    if not html:
        return []
    matches = list(_NOM_CONTAINER_RE.finditer(html))
    out: list[dict] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else min(len(html), m.start() + 2000)
        seg = html[m.end():end]
        mt = _NOM_TITLE_RE.search(seg)
        if not mt:
            continue
        title = re.sub(r"\s+", " ", mt.group(1)).strip()
        if not title:
            continue
        mp = _NOM_PERIOD_RE.search(seg)
        mr = _NOM_RESULT_RE.search(seg)
        out.append({
            "title": title,
            "period": re.sub(r"\s+", " ", mp.group(1)).strip() if mp else "",
            "result": re.sub(r"\s+", " ", mr.group(1)).strip() if mr else "",
            "winner": "winner-surface" in m.group(1),
        })

    seen: set[tuple] = set()
    result_list: list[dict] = []
    for n in out:
        key = (n["title"].lower(), n["result"].lower())
        if key not in seen:
            seen.add(key)
            result_list.append(n)
    return result_list[:12]


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
    html = await _get(f"/album/{slug}")
    if html is None:
        # заглушка/сеть — не кэшируем пустой результат
        log.info("rzt_rating_skip", slug=slug)
        return None
    data = _parse_rating(html)
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
        return await asyncio.wait_for(_rating_pipeline(artist, title), timeout=25)
    except Exception as e:
        log.warning("rzt_rating_timeout", artist=artist[:40], title=title[:40], error=str(e))
        return None
