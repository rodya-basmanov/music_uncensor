"""Менеджер кэша: hash → файл + telegram_file_id + TTL-очистка."""

import asyncio
import json
import os
import shutil
import time
from typing import Any, Dict, Optional

from utils.logger import get_logger

log = get_logger("cache")

# Не трогаем «сироты» моложе этого времени: их .json мог ещё не записаться,
# а загрузка — идти прямо сейчас.
_ORPHAN_GRACE_SEC = 3600


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

    def get_cache_stats(self) -> Dict[str, Any]:
        """Возвращает размер (ГБ) и число записей в кэше."""
        files = 0
        try:
            files = sum(1 for f in os.listdir(self.cache_dir) if f.endswith(".json"))
        except OSError:
            files = 0
        return {"size_gb": self.get_cache_size_gb(), "files": files}

    async def cleanup(self) -> int:
        """Очистка кэша: TTL + по размеру + сироты. Работа идёт в отдельном потоке."""
        async with self._lock:
            self._cached_size_gb = None
            return await asyncio.to_thread(self._cleanup_sync)

    def _cleanup_sync(self) -> int:
        deleted = 0
        now = time.time()
        ttl_seconds = self.ttl_days * 86400
        names = os.listdir(self.cache_dir)
        known = {f[:-5] for f in names if f.endswith(".json")}

        def _too_new(path: str) -> bool:
            """Файл/папка моложе grace-периода — не трогаем (возможна активная запись)."""
            try:
                return (now - os.path.getmtime(path)) < _ORPHAN_GRACE_SEC
            except OSError:
                return True

        # 0. Сироты: mp3/jpg без .json и временные work_* папки (только «старые»)
        for name in names:
            path = os.path.join(self.cache_dir, name)
            if name.startswith("work_") and os.path.isdir(path):
                if not _too_new(path):
                    shutil.rmtree(path, ignore_errors=True)
                continue
            base, ext = os.path.splitext(name)
            if ext.lower() in (".mp3", ".jpg", ".jpeg", ".png") and base not in known:
                if _too_new(path):
                    continue
                try:
                    os.unlink(path)
                    deleted += 1
                except OSError:
                    pass

        entries = []
        for filename in names:
            if not filename.endswith(".json"):
                continue
            hash_key = filename[:-5]
            meta_path = self._meta_path(hash_key)
            file_path = self._file_path(hash_key)
            cover_path = self._cover_path(hash_key)
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries.append((hash_key, data.get("accessed_at", 0), meta_path, file_path, cover_path))
            except Exception:
                continue

        # 1. Удаляем по TTL
        survivors = []
        for entry in entries:
            if now - entry[1] > ttl_seconds:
                self._delete_entry(entry[2], entry[3], entry[4])
                deleted += 1
            else:
                survivors.append(entry)

        # 2. По размеру: считаем объём один раз и вычитаем при удалении (не O(n²))
        sizes: dict[str, int] = {}
        total = 0
        for hk, at, mp, fp, cp in survivors:
            sz = 0
            for p in (fp, cp, mp):
                try:
                    sz += os.path.getsize(p)
                except OSError:
                    pass
            sizes[hk] = sz
            total += sz

        limit = self.max_size_gb * (1024 ** 3)
        if total > limit:
            target = limit * 0.8
            survivors.sort(key=lambda x: x[1])  # старейшие — первыми
            for hk, at, mp, fp, cp in survivors:
                if total <= target:
                    break
                self._delete_entry(mp, fp, cp)
                deleted += 1
                total -= sizes.get(hk, 0)

        if deleted:
            log.info("cache_cleanup_done", deleted=deleted)
        return deleted

    def _delete_entry(self, *paths: str) -> None:
        """Удаляет файлы записи кэша."""
        for path in paths:
            try:
                if path and os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass
