"""Получение треков из плейлиста ВКонтакте через vk_api."""

import asyncio
from typing import List, Optional

import vk_api

from core.scraper.common import Candidate
from utils.helpers import Track
from utils.logger import get_logger

log = get_logger("vk_fetcher")


def _cover_from_item(item: dict) -> str:
    """Достаёт URL обложки альбома из элемента audio.get."""
    album = item.get("album") or {}
    thumb = album.get("thumb") or {}
    for key in ("photo_1200", "photo_600", "photo_300", "photo_270", "photo"):
        if thumb.get(key):
            return thumb[key]
    return ""


class VKFetcher:
    """Обёртка для работы с аудио VK через vk_api."""

    def __init__(self, token: str):
        self.token = token
        self._session = None
        self._api = None

    def _ensure_session(self) -> None:
        """Создаёт сессию VK API если ещё не создана."""
        if self._session is None:
            self._session = vk_api.VkApi(token=self.token, api_version="5.131")
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
        tracks: List[Track] = []
        seen_ids: set = set()
        offset = 0
        page_size = 100

        try:
            while len(tracks) < count:
                want = min(page_size, count - len(tracks))
                items = self._fetch_playlist_items(
                    owner_id=owner_id,
                    playlist_id=playlist_id,
                    access_key=access_key,
                    count=want,
                    offset=offset,
                )
                if not items:
                    break

                for item in items:
                    key = f"{item.get('owner_id')}_{item.get('id')}"
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)

                    artist = (item.get("artist") or "").strip()
                    title = (item.get("title") or "").strip()
                    if not title:
                        continue
                    url = (item.get("url") or "").strip()
                    tracks.append(Track(
                        artist=artist,
                        title=title,
                        duration=item.get("duration", 0) or 0,
                        url=url,
                        cover_url=_cover_from_item(item),
                        source="vk" if url else "",
                    ))

                if len(items) < want:
                    break
                offset += len(items)

            log.info(
                "vk_playlist_fetched",
                owner_id=owner_id,
                playlist_id=playlist_id,
                tracks_count=len(tracks),
            )
        except vk_api.exceptions.ApiError as e:
            log.error("vk_api_error", error=str(e), owner_id=owner_id, playlist_id=playlist_id)
        except Exception as e:
            log.error("vk_fetch_error", error=str(e))

        return tracks


    def _fetch_playlist_items(
        self,
        owner_id: int,
        playlist_id: int,
        access_key: Optional[str],
        count: int,
        offset: int = 0,
    ) -> list:
        """Пробует несколько методов VK API для получения треков плейлиста."""
        audio_get_params = {
            "owner_id": owner_id,
            "album_id": playlist_id,
            "count": count,
            "offset": offset,
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

    def search(self, query: str, count: int = 5) -> List[Candidate]:
        """Поиск аудио в VK (audio.search). Возвращает кандидатов с прямыми ссылками."""
        self._ensure_session()
        try:
            response = self._api.audio.search(q=query, count=count)
        except Exception as e:
            log.warning("vk_search_error", query=query, error=str(e))
            return []

        items = response.get("items", []) if isinstance(response, dict) else []
        results: List[Candidate] = []
        for item in items:
            artist = (item.get("artist") or "").strip()
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url:
                continue
            results.append(Candidate(
                url=url,
                title=title,
                artist=artist,
                uploader=artist,
                duration=item.get("duration", 0) or 0,
                cover_url=_cover_from_item(item),
                source="vk",
            ))
        log.info("vk_search_done", query=query, count=len(results))
        return results

    async def search_async(self, query: str, count: int = 5) -> List[Candidate]:
        """Асинхронная обёртка поиска."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.search, query, count)

    def get_track(self, owner_id: int, audio_id: int, access_key: Optional[str] = None) -> Optional[Track]:
        """Получает одиночный трек по (owner_id, audio_id) через audio.getById."""
        self._ensure_session()
        ref = f"{owner_id}_{audio_id}" + (f"_{access_key}" if access_key else "")
        try:
            response = self._api.audio.getById(audios=ref)
        except Exception as e:
            log.warning("vk_getbyid_error", ref=ref, error=str(e))
            return None

        items = response if isinstance(response, list) else response.get("items", [])
        if not items:
            return None
        item = items[0]
        return Track(
            artist=(item.get("artist") or "").strip(),
            title=(item.get("title") or "").strip(),
            duration=item.get("duration", 0) or 0,
            url=(item.get("url") or "").strip(),
            cover_url=_cover_from_item(item),
            source="vk" if item.get("url") else "",
        )

    async def get_track_async(self, owner_id: int, audio_id: int, access_key: Optional[str] = None) -> Optional[Track]:
        """Асинхронная обёртка получения одиночного трека."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.get_track, owner_id, audio_id, access_key)
