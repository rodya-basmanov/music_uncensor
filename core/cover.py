"""Загрузка обложек (VK/источник) через aiohttp с опциональным прокси."""


import aiohttp
from aiohttp_socks import ProxyConnector

from utils.logger import get_logger

log = get_logger("cover")

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

_sessions: dict[str, aiohttp.ClientSession] = {}


def _session(proxy: str) -> aiohttp.ClientSession:
    """Переиспользуемая aiohttp-сессия (отдельная для каждого прокси)."""
    s = _sessions.get(proxy)
    if s is None or s.closed:
        connector = ProxyConnector.from_url(proxy) if proxy else None
        s = aiohttp.ClientSession(connector=connector)
        _sessions[proxy] = s
    return s


async def download_image(url: str, dest_path: str, proxy: str = "",
                         timeout: int = 10, retries: int = 2) -> bool:
    """Асинхронно скачивает изображение (при заданном proxy — через него)."""
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
                with open(dest_path, "wb") as f:
                    f.write(data)
                return True
            last_err = "empty body"
        except Exception as e:
            last_err = str(e)

    log.warning("cover_download_error", url=url[:80], error=last_err)
    return False
