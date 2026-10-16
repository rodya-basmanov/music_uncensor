"""Загрузка обложек (VK/источник) через aiohttp с опциональным прокси."""


import asyncio
from collections import OrderedDict

import aiohttp
from aiohttp_socks import ProxyConnector

from utils.logger import get_logger

log = get_logger("cover")

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

_sessions: dict[str, aiohttp.ClientSession] = {}
_raw_cache: "OrderedDict[str, bytes]" = OrderedDict()
_locks: dict[str, asyncio.Lock] = {}
_RAW_CACHE_MAX = 64


def _lock(url: str) -> asyncio.Lock:
    """Замок на URL: параллельные запросы одной обложки не дублируют загрузку."""
    lock = _locks.get(url)
    if lock is None:
        lock = asyncio.Lock()
        _locks[url] = lock
    return lock


def _session(proxy: str) -> aiohttp.ClientSession:
    """Переиспользуемая aiohttp-сессия (отдельная для каждого прокси)."""
    s = _sessions.get(proxy)
    if s is None or s.closed:
        connector = ProxyConnector.from_url(proxy) if proxy else None
        s = aiohttp.ClientSession(connector=connector)
        _sessions[proxy] = s
    return s


async def _fetch_bytes(url: str, proxy: str, timeout: int, retries: int) -> bytes:
    last_err = ""
    session = _session(proxy)
    for _ in range(retries):
        try:
            async with session.get(
                url,
                headers={"User-Agent": _UA},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    last_err = f"HTTP {resp.status}"
                    continue
                data = await resp.read()
            if data:
                return data
            last_err = "empty body"
        except Exception as e:
            last_err = str(e)
    log.warning("cover_download_error", url=url[:80], error=last_err)
    return b""


async def download_image(url: str, dest_path: str, proxy: str = "",
                         timeout: int = 10, retries: int = 2) -> bool:
    """Скачивает изображение (с кэшем по URL — альбомная обложка не качается повторно)."""
    data = _raw_cache.get(url)
    if data is None:
        async with _lock(url):
            data = _raw_cache.get(url)
            if data is None:
                data = await _fetch_bytes(url, proxy, timeout, retries)
                if not data:
                    return False
                _raw_cache[url] = data
                while len(_raw_cache) > _RAW_CACHE_MAX:
                    _raw_cache.popitem(last=False)
    else:
        _raw_cache.move_to_end(url)
    try:
        with open(dest_path, "wb") as f:
            f.write(data)
        return True
    except OSError as e:
        log.warning("cover_write_error", error=str(e))
        return False
