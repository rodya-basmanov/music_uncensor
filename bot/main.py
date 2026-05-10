"""Точка входа бота — инициализация и запуск polling."""

import asyncio
import sys
import os

# Добавляем корневую директорию в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from python_socks import ProxyError

from bot.handlers import start, link, playlist, status
from core.cache_manager import CacheManager
from core.downloader import Downloader
from core.scraper.mp3party import Mp3PartyScraper
from utils.config import load_config
from utils.logger import setup_logging, get_logger
from utils.storage import UserStorage


async def main():
    """Главная функция запуска бота."""
    # Загружаем конфиг
    config = load_config()

    # Настраиваем логирование
    setup_logging(config.log_level)
    log = get_logger("main")

    # Проверяем обязательные параметры
    if not config.bot_token:
        log.error("BOT_TOKEN не задан в .env!")
        sys.exit(1)

    log.info("starting_bot", log_level=config.log_level)

    # Инициализируем компоненты
    storage = UserStorage("./data/users.json")
    cache_mgr = CacheManager(
        cache_dir=config.cache_dir,
        ttl_days=config.cache_ttl_days,
        max_size_gb=config.max_cache_size_gb,
    )
    scraper = Mp3PartyScraper(rate_limit=config.rate_limit_search)
    downloader = Downloader(cache_dir=config.cache_dir)

    # Создаём бота
    session = None
    if config.telegram_proxy_url:
        session = AiohttpSession(proxy=config.telegram_proxy_url)
        log.info("telegram_proxy_enabled")
    bot = Bot(token=config.bot_token, session=session)
    dp = Dispatcher()

    # Инжектим зависимости в хендлеры
    link.set_storage(storage)
    playlist.set_dependencies(
        scraper=scraper,
        downloader=downloader,
        cache_mgr=cache_mgr,
        storage=storage,
        fuzzy_threshold=config.fuzzy_threshold,
        max_playlist_size=config.max_playlist_size,
        rate_limit=config.rate_limit_search,
    )

    # Регистрируем роутеры
    dp.include_router(start.router)
    dp.include_router(link.router)
    dp.include_router(status.router)
    dp.include_router(playlist.router)  # Последним — чтобы не перехватывал команды

    # Периодическая очистка кэша (каждые 6 часов)
    async def cache_cleanup_loop():
        while True:
            await asyncio.sleep(6 * 3600)  # 6 часов
            try:
                deleted = cache_mgr.cleanup()
                if deleted:
                    log.info("scheduled_cache_cleanup", deleted=deleted)
            except Exception as e:
                log.error("cache_cleanup_error", error=str(e))

    asyncio.create_task(cache_cleanup_loop())

    # Запускаем polling с повторными попытками при временных сетевых сбоях Telegram
    retry_delay = 5
    max_retry_delay = 60
    try:
        while True:
            try:
                me = await bot.me()
                log.info("bot_started", bot_id=me.id)
                await dp.start_polling(bot)
                break
            except (TelegramNetworkError, ProxyError) as e:
                log.warning(
                    "telegram_network_error",
                    error=str(e),
                    retry_in_seconds=retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())


