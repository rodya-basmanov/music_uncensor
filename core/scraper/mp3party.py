"""
Скрапер mp3party.net — поиск треков и получение прямых ссылок на mp3.

Структура HTML (найдено исследованием):
- Поиск: GET /search?q={query}
- Результаты содержат <div> с data-атрибутами:
  - data-js-artist-name: имя артиста
  - data-js-song-title: название трека
  - data-js-url: прямая ссылка на mp3 (https://dl2.mp3party.net/online/{id}.mp3)
  - data-js-id: ID трека
"""

import asyncio
from dataclasses import dataclass
from typing import List

import aiohttp
from bs4 import BeautifulSoup

from utils.helpers import random_ua
from utils.logger import get_logger

log = get_logger("mp3party")


@dataclass
class Mp3PartyTrack:
    """Результат поиска на mp3party."""
    artist: str
    title: str
    mp3_url: str
    track_id: str
    page_url: str


class Mp3PartyScraper:
    """Скрапер для mp3party.net с использованием aiohttp."""

    BASE_URL = "https://mp3party.net"
    SEARCH_URL = f"{BASE_URL}/search"

    def __init__(self, rate_limit: float = 2.0):
        self.rate_limit = rate_limit
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Получает или создаёт aiohttp сессию."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": random_ua(),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                }
            )
        return self._session

    async def close(self) -> None:
        """Закрывает aiohttp сессию."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def search(self, artist: str, title: str) -> List[Mp3PartyTrack]:
        """
        Ищет треки на mp3party.net (асинхронно).

        Пробует несколько вариантов запроса и объединяет результаты.

        Args:
            artist: Имя артиста
            title: Название трека

        Returns:
            Список найденных треков с прямыми ссылками на mp3
        """
        all_results: List[Mp3PartyTrack] = []
        existing_ids: set = set()

        async def add_unique(new_results: List[Mp3PartyTrack]) -> None:
            for r in new_results:
                if r.track_id not in existing_ids:
                    all_results.append(r)
                    existing_ids.add(r.track_id)

        # 1. Поиск "Artist Title"
        await add_unique(await self._do_search(f"{artist} {title}"))

        # 2. Поиск "Title Artist" — mp3party часто ищет лучше в этом порядке
        if len(all_results) < 5:
            await add_unique(await self._do_search(f"{title} {artist}"))

        # 3. Поиск по артисту
        if len(all_results) < 5:
            await add_unique(await self._do_search(artist))

        # 4. Поиск по названию
        if len(all_results) < 5:
            await add_unique(await self._do_search(title))

        log.info("mp3party_search_done", query=f"{artist} {title}", results=len(all_results))
        return all_results

    async def _do_search(self, query: str) -> List[Mp3PartyTrack]:
        """Выполняет один поисковый запрос к mp3party.net."""
        # Убираем точки — mp3party ищет лучше без них (J. ROUH → J ROUH)
        query = query.replace(".", " ").replace("  ", " ").strip()

        session = await self._get_session()

        try:
            async with session.get(
                self.SEARCH_URL,
                params={"q": query},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status != 200:
                    log.warning("mp3party_search_failed", query=query, status=response.status)
                    return []

                html = await response.text()
        except asyncio.TimeoutError:
            log.warning("mp3party_search_timeout", query=query)
            return []
        except Exception as e:
            log.warning("mp3party_search_error", query=query, error=str(e))
            return []

        soup = BeautifulSoup(html, "lxml")
        results = []

        for item in soup.find_all(attrs={"data-js-url": True}):
            track_artist = item.get("data-js-artist-name", "").strip()
            track_title = item.get("data-js-song-title", "").strip()
            mp3_url = item.get("data-js-url", "").strip()
            track_id = item.get("data-js-id", "").strip()

            if track_artist and track_title and mp3_url:
                results.append(Mp3PartyTrack(
                    artist=track_artist,
                    title=track_title,
                    mp3_url=mp3_url,
                    track_id=track_id,
                    page_url=f"{self.BASE_URL}/music/{track_id}",
                ))

        return results

    async def search_async(self, artist: str, title: str) -> List[Mp3PartyTrack]:
        """Асинхронный интерфейс для поиска (теперь основной)."""
        return await self.search(artist, title)
