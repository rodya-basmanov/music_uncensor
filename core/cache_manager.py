"""Менеджер кэша: hash → файл + telegram_file_id + TTL-очистка."""

import asyncio
import json
import os
import time
from typing import Any, Dict, Optional

from utils.logger import get_logger

log = get_logger("cache")


class CacheManager:
    """Управление кэшем аудиофайлов и метаданных."""

    def __init__(self, cache_dir: str = "./data/cache", ttl_days: int = 7, max_size_gb: int = 20):
        self.cache_dir = cache_dir
        self.ttl_days = ttl_days
        self.max_size_gb = max_size_gb
        os.makedirs(cache_dir, exist_ok=True)
        self._lock = asyncio.Lock()
        self._cached_size_gb: float | None = None
        self._last_size_check: float = 0

    def _meta_path(self, hash_key: str) -> str:
        """Путь к JSON-файлу метаданных."""
        return os.path.join(self.cache_dir, f"{hash_key}.json")

    def _file_path(self, hash_key: str) -> str:
        """Путь к mp3-файлу."""
        return os.path.join(self.cache_dir, f"{hash_key}.mp3")

    def _cover_path(self, hash_key: str) -> str:
        """Путь к jpg-обложке."""
        return os.path.join(self.cache_dir, f"{hash_key}.jpg")

    def get(self, hash_key: str) -> Optional[Dict[str, Any]]:
        """
        Получает метаданные из кэша по хешу.

        Returns:
            Словарь метаданных или None
        """
        meta_path = self._meta_path(hash_key)
        if not os.path.exists(meta_path):
            return None

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except (json.JSONDecodeError, OSError) as e:
            log.warning("cache_read_error", hash_key=hash_key, error=str(e))
            return None

    def save(self, hash_key: str, metadata: Dict[str, Any]) -> None:
        """Сохраняет метаданные в кэш."""
        meta_path = self._meta_path(hash_key)
        metadata["accessed_at"] = int(time.time())

        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            log.info("cache_saved", hash_key=hash_key[:16])
        except OSError as e:
            log.error("cache_save_error", hash_key=hash_key, error=str(e))

    def save_file_id(self, hash_key: str, file_id: str) -> None:
        """Обновляет telegram_file_id в существующей записи кэша."""
        data = self.get(hash_key)
        if data:
            data["telegram_file_id"] = file_id
            data["accessed_at"] = int(time.time())
            self.save(hash_key, data)
            log.info("file_id_cached", hash_key=hash_key[:16])

    def save_cover_file_id(self, hash_key: str, cover_file_id: str) -> None:
        """Обновляет cover_file_id (file_id обложки в Telegram) в кэше."""
        data = self.get(hash_key)
        if data and cover_file_id:
            data["cover_file_id"] = cover_file_id
            data["accessed_at"] = int(time.time())
            self.save(hash_key, data)
            log.info("cover_file_id_cached", hash_key=hash_key[:16])

    def update_access(self, hash_key: str) -> None:
        """Обновляет время последнего доступа (продлевает жизнь в кэше)."""
        data = self.get(hash_key)
        if data:
            data["accessed_at"] = int(time.time())
            self.save(hash_key, data)

    def has_file(self, hash_key: str) -> bool:
        """Проверяет, существует ли mp3-файл в кэше."""
        file_path = self._file_path(hash_key)
        return os.path.exists(file_path) and os.path.getsize(file_path) > 0

    def get_cache_size_gb(self, force: bool = False) -> float:
        """Возвращает текущий размер кэша в ГБ (с кэшированием)."""
        now = time.time()
        # Используем кэш если он свежее 60 секунд
        if not force and self._cached_size_gb is not None and (now - self._last_size_check) < 60:
            return self._cached_size_gb
        total = 0
        for f in os.listdir(self.cache_dir):
            fp = os.path.join(self.cache_dir, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
        self._cached_size_gb = total / (1024 ** 3)
        self._last_size_check = now
        return self._cached_size_gb

    async def cleanup(self) -> int:
        """
        Очистка кэша: удаляет записи старше TTL или при превышении размера.

        Returns:
            Количество удалённых файлов
        """
        async with self._lock:
            # Сброс кэша размера для точного измерения
            self._cached_size_gb = None
            deleted = 0
            now = time.time()
            ttl_seconds = self.ttl_days * 86400
            entries = []

            # Собираем все записи
            for filename in os.listdir(self.cache_dir):
                if not filename.endswith(".json"):
                    continue

                hash_key = filename[:-5]
                meta_path = self._meta_path(hash_key)
                file_path = self._file_path(hash_key)
                cover_path = self._cover_path(hash_key)

                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    accessed_at = data.get("accessed_at", 0)
                    entries.append((hash_key, accessed_at, meta_path, file_path, cover_path))
                except Exception:
                    continue

            # 1. Удаляем по TTL
            for hash_key, accessed_at, meta_path, file_path, cover_path in entries:
                if now - accessed_at > ttl_seconds:
                    self._delete_entry(meta_path, file_path, cover_path)
                    deleted += 1
                    continue

            # 2. Удаляем старые при превышении размера
            if self.get_cache_size_gb() > self.max_size_gb:
                remaining = [
                    (hk, at, mp, fp, cp) for hk, at, mp, fp, cp in entries
                    if os.path.exists(mp)
                ]
                remaining.sort(key=lambda x: x[1])

                while remaining and self.get_cache_size_gb() > self.max_size_gb * 0.8:
                    _, _, meta_path, file_path, cover_path = remaining.pop(0)
                    self._delete_entry(meta_path, file_path, cover_path)
                    deleted += 1

            if deleted:
                log.info("cache_cleanup_done", deleted=deleted, size_gb=round(self.get_cache_size_gb(), 2))

            return deleted

    def _delete_entry(self, *paths: str) -> None:
        """Удаляет файлы записи кэша."""
        for path in paths:
            try:
                if path and os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass
