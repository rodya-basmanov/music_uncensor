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
import random
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import quote

import requests
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
    """Скрапер для mp3party.net."""

    BASE_URL = "https://mp3party.net"
    SEARCH_URL = f"{BASE_URL}/search"

    def __init__(self, rate_limit: float = 2.0):
        self.rate_limit = rate_limit
        self.session = requests.Session()
        self._update_headers()

    def _update_headers(self) -> None:
        """Обновляет заголовки сессии с рандомным UA."""
        self.session.headers.update({
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": f"{self.BASE_URL}/",
        })

    def search(self, artist: str, title: str) -> List[Mp3PartyTrack]:
        """
        Ищет треки на mp3party.net.
        
        Args:
            artist: Имя артиста
            title: Название трека
            
        Returns:
            Список найденных треков с прямыми ссылками на mp3
        """
        query = f"{artist} {title}"
        self._update_headers()

        try:
            response = self.session.get(
                self.SEARCH_URL,
                params={"q": query},
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            log.error("mp3party_search_failed", query=query, error=str(e))
            return []

        soup = BeautifulSoup(response.text, "lxml")
        results = []

        # Парсим элементы с data-js-url (прямые ссылки на mp3)
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

        log.info("mp3party_search_done", query=query, results=len(results))
        return results

    async def search_async(self, artist: str, title: str) -> List[Mp3PartyTrack]:
        """Асинхронная обёртка над search()."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.search, artist, title)
