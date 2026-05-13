"""Скачивание файлов: requests (быстро) + Playwright XHR fallback."""

import asyncio
import base64
import os
import time
from typing import Optional

import requests
from playwright.sync_api import sync_playwright

from utils.helpers import random_ua
from utils.logger import get_logger

log = get_logger("downloader")

_MIN_FILE_SIZE = 1024  # Минимальный размер корректного MP3 — 1KB


class Downloader:
    """Скачивает mp3-файлы: requests (быстро), Playwright XHR (fallback)."""

    def __init__(self, cache_dir: str = "./data/cache", max_retries: int = 1):
        self.cache_dir = cache_dir
        self.max_retries = max_retries
        os.makedirs(cache_dir, exist_ok=True)
        self._pw_page = None
        self._pw_browser = None
        self._pw_context = None

    def _ensure_playwright(self):
        """Инициализирует Playwright-сессию (один раз, переиспользуется)."""
        if self._pw_page is not None:
            try:
                # Проверяем что страница жива
                self._pw_page.evaluate("1+1")
                return
            except Exception:
                self._close_playwright()

        log.info("playwright_initializing")
        try:
            pw = sync_playwright().start()
            self._pw = pw
            self._pw_browser = pw.chromium.launch(headless=True)
            self._pw_context = self._pw_browser.new_context(
                user_agent=random_ua(),
            )
            self._pw_page = self._pw_context.new_page()

            # Заходим на mp3party — получаем сессию
            self._pw_page.goto(
                "https://mp3party.net/",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            time.sleep(2)
            log.info("playwright_ready")
        except Exception as e:
            log.warning("playwright_init_failed", error=str(e))
            self._close_playwright()

    def _close_playwright(self):
        """Закрывает Playwright-сессию."""
        for obj in [self._pw_page, self._pw_context, self._pw_browser]:
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        if hasattr(self, '_pw') and self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw_page = None
        self._pw_context = None
        self._pw_browser = None
        self._pw = None

    def download(self, url: str, hash_key: str, track_page_url: str = "", max_retries: int = 3) -> Optional[str]:
        """
        Скачивает файл по URL с retry.

        1. Пробует requests (быстро)
        2. Если битый — Playwright XHR fallback
        3. Повторяет несколько раз при неудаче

        Args:
            url: Прямая ссылка на mp3
            hash_key: SHA256-хеш для имени файла
            track_page_url: URL страницы трека (для Referer)
            max_retries: Количество попыток

        Returns:
            Путь к скачанному файлу или None при ошибке
        """
        dest_path = os.path.join(self.cache_dir, f"{hash_key}.mp3")

        # Если файл уже существует и валиден — не скачиваем повторно
        if os.path.exists(dest_path) and os.path.getsize(dest_path) >= _MIN_FILE_SIZE:
            log.info("file_already_exists", path=dest_path)
            return dest_path

        for attempt in range(max_retries):
            # Удаляем битый файл перед каждой попыткой
            if os.path.exists(dest_path):
                os.remove(dest_path)

            referer = track_page_url or "https://mp3party.net/"

            # 1. Быстрая попытка через requests
            result = self._download_with_requests(url, dest_path, referer)
            if result:
                return result

            # 2. Playwright XHR fallback
            log.info("playwright_fallback_attempt", url=url, attempt=attempt + 1)
            result = self._download_with_playwright_xhr(url, dest_path, track_page_url)
            if result:
                return result

            log.warning("download_attempt_failed", url=url, attempt=attempt + 1, max_retries=max_retries)

            # Пауза между попытками (кроме последней)
            if attempt < max_retries - 1:
                time.sleep(1 + attempt)  # 1 сек, 2 сек, 3 сек...

        log.warning("download_failed_all_methods", url=url)
        return None

    def _download_with_requests(self, url: str, dest_path: str, referer: str) -> Optional[str]:
        """Быстрое скачивание через requests."""
        try:
            headers = {
                "User-Agent": random_ua(),
                "Referer": referer,
                "Accept": "*/*",
            }

            response = requests.get(url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if "html" in content_type:
                log.warning("download_got_html", url=url)
                return None

            # Ранняя проверка первого чанка
            first_chunk = True
            with open(dest_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        if first_chunk:
                            first_chunk = False
                            if not (chunk[:3] == b'ID3' or (len(chunk) > 0 and chunk[0] == 0xff)):
                                log.warning("download_not_mp3", url=url, first_bytes=chunk[:30])
                                break
                        f.write(chunk)

            file_size = os.path.getsize(dest_path) if os.path.exists(dest_path) else 0
            if file_size >= _MIN_FILE_SIZE:
                log.info("download_complete", url=url, path=dest_path,
                         size_mb=round(file_size / 1024 / 1024, 2), method="requests")
                return dest_path

            if os.path.exists(dest_path):
                os.remove(dest_path)
            return None

        except requests.RequestException as e:
            log.warning("download_requests_failed", url=url, error=str(e))
            if os.path.exists(dest_path):
                os.remove(dest_path)
            return None

    def _download_with_playwright_xhr(self, url: str, dest_path: str, track_page_url: str = "") -> Optional[str]:
        """Скачивание через Playwright: XHR внутри браузера, результат — base64."""
        try:
            self._ensure_playwright()
            if self._pw_page is None:
                return None

            # Если есть page_url — заходим на страницу трека для куки/сессии
            if track_page_url:
                try:
                    self._pw_page.goto(
                        track_page_url,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    time.sleep(1)
                except Exception as e:
                    log.warning("playwright_goto_failed", url=track_page_url, error=str(e))

            # XHR-запрос внутри браузера — сервер видит полноценную сессию
            b64 = self._pw_page.evaluate(f'''async () => {{
                return new Promise((resolve, reject) => {{
                    const xhr = new XMLHttpRequest();
                    xhr.open('GET', '{url}', true);
                    xhr.responseType = 'arraybuffer';
                    xhr.onload = () => {{
                        const arr = new Uint8Array(xhr.response);
                        let binary = '';
                        for (let i = 0; i < arr.length; i++) {{
                            binary += String.fromCharCode(arr[i]);
                        }}
                        resolve(btoa(binary));
                    }};
                    xhr.onerror = () => reject('XHR error');
                    xhr.send();
                }});
            }}''')

            body = base64.b64decode(b64)
            with open(dest_path, "wb") as f:
                f.write(body)

            file_size = os.path.getsize(dest_path)
            if file_size >= _MIN_FILE_SIZE:
                log.info("download_complete", url=url, path=dest_path,
                         size_mb=round(file_size / 1024 / 1024, 2), method="playwright_xhr")
                return dest_path

            log.warning("download_playwright_too_small", url=url, size_bytes=file_size)
            if os.path.exists(dest_path):
                os.remove(dest_path)
            return None

        except Exception as e:
            log.warning("download_playwright_error", url=url, error=str(e))
            # Переинициализируем Playwright при ошибке
            self._close_playwright()
            if os.path.exists(dest_path):
                os.remove(dest_path)
            return None

    async def download_async(self, url: str, hash_key: str, track_page_url: str = "") -> Optional[str]:
        """Асинхронная обёртка."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.download, url, hash_key, track_page_url)

    def __del__(self):
        self._close_playwright()
