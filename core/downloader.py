"""Скачивание файлов с retry и задержками."""

import asyncio
import os
import time
from typing import Optional

import requests

from utils.helpers import random_ua
from utils.logger import get_logger

log = get_logger("downloader")


class Downloader:
    """Скачивает mp3-файлы с retry и exponential backoff."""

    def __init__(self, cache_dir: str = "./data/cache", max_retries: int = 3):
        self.cache_dir = cache_dir
        self.max_retries = max_retries
        os.makedirs(cache_dir, exist_ok=True)

    def download(self, url: str, hash_key: str) -> Optional[str]:
        """
        Скачивает файл по URL.
        
        Args:
            url: Прямая ссылка на mp3
            hash_key: SHA256-хеш для имени файла
            
        Returns:
            Путь к скачанному файлу или None при ошибке
        """
        dest_path = os.path.join(self.cache_dir, f"{hash_key}.mp3")

        # Если файл уже существует — не скачиваем повторно
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            log.info("file_already_exists", path=dest_path)
            return dest_path

        for attempt in range(1, self.max_retries + 1):
            try:
                headers = {
                    "User-Agent": random_ua(),
                    "Referer": "https://mp3party.net/",
                    "Accept": "*/*",
                }

                response = requests.get(
                    url,
                    headers=headers,
                    stream=True,
                    timeout=30,
                )
                response.raise_for_status()

                # Проверяем Content-Type
                content_type = response.headers.get("Content-Type", "")
                if "html" in content_type:
                    log.warning(
                        "download_got_html",
                        url=url,
                        attempt=attempt,
                    )
                    if attempt < self.max_retries:
                        time.sleep(2 ** attempt)
                        continue
                    return None

                # Стриминговое скачивание
                with open(dest_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

                file_size = os.path.getsize(dest_path)
                log.info(
                    "download_complete",
                    url=url,
                    path=dest_path,
                    size_mb=round(file_size / 1024 / 1024, 2),
                )
                return dest_path

            except requests.RequestException as e:
                log.warning(
                    "download_attempt_failed",
                    url=url,
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    log.error("download_failed", url=url, error=str(e))

        return None

    async def download_async(self, url: str, hash_key: str) -> Optional[str]:
        """Асинхронная обёртка."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.download, url, hash_key)
