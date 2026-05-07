"""Получение треков из плейлиста ВКонтакте через vk_api."""

import asyncio
from typing import List, Optional

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
            items = self._fetch_playlist_items(
                owner_id=owner_id,
                playlist_id=playlist_id,
                access_key=access_key,
                count=min(count, 100),
            )

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


    def _fetch_playlist_items(
        self,
        owner_id: int,
        playlist_id: int,
        access_key: Optional[str],
        count: int,
    ) -> list:
        """Пробует несколько методов VK API для получения треков плейлиста."""
        audio_get_params = {
            "owner_id": owner_id,
            "album_id": playlist_id,
            "count": count,
        }
        playlist_by_id_params = {
            "owner_id": owner_id,
            "playlist_id": playlist_id,
        }
        if access_key:
            audio_get_params["access_key"] = access_key
            playlist_by_id_params["access_key"] = access_key

        method_attempts = [
            ("audio.get", lambda: self._api.audio.get(**audio_get_params)),
            (
                "audio.getPlaylistById",
                lambda: self._api.audio.getPlaylistById(**playlist_by_id_params),
            ),
        ]

        last_error = None
        for method_name, caller in method_attempts:
            try:
                response = caller()
                log.info("vk_method_success", method=method_name)

                if isinstance(response, dict):
                    if "items" in response and isinstance(response["items"], list):
                        return response["items"]

                    # audio.getPlaylistById обычно возвращает словарь плейлиста с полем audios
                    audios = response.get("audios")
                    if isinstance(audios, list):
                        return [item for item in audios if isinstance(item, dict)]

                return []
            except vk_api.exceptions.ApiError as e:
                last_error = e
                # [3] Unknown method passed — пробуем следующий метод
                if "Unknown method passed" in str(e):
                    log.warning("vk_method_unknown", method=method_name, error=str(e))
                    continue
                raise

        if last_error:
            raise last_error

        return []

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
