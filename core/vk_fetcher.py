"""Получение треков из плейлиста ВКонтакте через vk_api."""

import asyncio
import re
from typing import List, Optional, Tuple

import vk_api

from utils.helpers import Track
from utils.logger import get_logger

log = get_logger("vk_fetcher")


class VKFetcher:
    """Обёртка для работы с аудио VK через vk_api."""

    def __init__(self, token: str):
        self.token = token
        self._session = None
        self._api = None

    def _ensure_session(self) -> None:
        """Создаёт сессию VK API если ещё не создана."""
        if self._session is None:
            self._session = vk_api.VkApi(token=self.token)
            self._api = self._session.get_api()

    def get_playlist_tracks(
        self,
        owner_id: int,
        playlist_id: int,
        access_key: Optional[str] = None,
        count: int = 100,
    ) -> List[Track]:
        """
        Получает список треков из плейлиста VK.
        
        Args:
            owner_id: ID владельца плейлиста
            playlist_id: ID плейлиста (album_id)
            access_key: Ключ доступа (для приватных плейлистов)
            count: Макс. количество треков
            
        Returns:
            Список Track
        """
        self._ensure_session()
        tracks = []

        try:
            params = {
                "owner_id": owner_id,
                "album_id": playlist_id,
                "count": min(count, 100),
            }
            if access_key:
                params["access_key"] = access_key

            response = self._api.audio.get(**params)
            items = response.get("items", [])

            for item in items:
                artist = item.get("artist", "").strip()
                title = item.get("title", "").strip()
                duration = item.get("duration", 0)

                if artist and title:
                    tracks.append(Track(
                        artist=artist,
                        title=title,
                        duration=duration,
                    ))

            log.info(
                "vk_playlist_fetched",
                owner_id=owner_id,
                playlist_id=playlist_id,
                tracks_count=len(tracks),
            )
        except vk_api.exceptions.ApiError as e:
            log.error(
                "vk_api_error",
                error=str(e),
                owner_id=owner_id,
                playlist_id=playlist_id,
            )
        except Exception as e:
            log.error("vk_fetch_error", error=str(e))

        return tracks

    async def get_playlist_tracks_async(
        self,
        owner_id: int,
        playlist_id: int,
        access_key: Optional[str] = None,
        count: int = 100,
    ) -> List[Track]:
        """Асинхронная обёртка."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.get_playlist_tracks, owner_id, playlist_id, access_key, count
        )
