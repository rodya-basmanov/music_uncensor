"""Точка входа бота — инициализация и запуск polling."""

import asyncio
import signal
import sys
import os

# Добавляем корневую директорию в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sentry_sdk
from sentry_sdk import capture_exception

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from python_socks import ProxyError

from bot.handlers import start, link, playlist, status, cancel, stats
from core.cache_manager import CacheManager
from core.downloader import Downloader
from core.scraper.mp3party import Mp3PartyScraper
from utils.config import load_config
from utils.logger import setup_logging, get_logger
from utils.storage import UserStorage

# Глобальные переменные для graceful shutdown
_shutdown_event: asyncio.Event | None = None
_log = None


async def _shutdown(sig_name: str, scraper: Mp3PartyScraper, cache_mgr: CacheManager, bot: Bot):
    """Graceful shutdown — корректно закрывает все ресурсы."""
    global _log
    if _log:
        _log.info("shutdown_initiated", signal=sig_name)

    # Останавливаем приём новых событий
    if _shutdown_event:
        _shutdown_event.set()

    # Очищаем кэш при выключении
    try:
        deleted = cache_mgr.cleanup()
        if deleted and _log:
            _log.info("cache_cleanup_on_shutdown", deleted=deleted)
    except Exception as e:
        if _log:
            _log.error("cache_cleanup_error", error=str(e))

    # Закрываем aiohttp сессию скрейпера
    try:
        await scraper.close()
    except Exception as e:
        if _log:
            _log.error("scraper_close_error", error=str(e))

    # Закрываем сессию бота
    try:
        await bot.session.close()
    except Exception as e:
        if _log:
            _log.error("bot_session_close_error", error=str(e))

    if _log:
        _log.info("shutdown_complete")


async def main():
    """Главная функция запуска бота."""
    global _shutdown_event, _log

    # Загружаем конфиг
    config = load_config()

    # Настраиваем логирование
    setup_logging(config.log_level)
    _log = get_logger("main")

    # Инициализируем Sentry (если DSN указан)
    if config.sentry_dsn:
        sentry_sdk.init(
            dsn=config.sentry_dsn,
            traces_sample_rate=0.1,
            environment="production",
        )
        _log.info("sentry_enabled")

    # Проверяем обязательные параметры
    if not config.bot_token:
        _log.error("BOT_TOKEN не задан в .env!")
        sys.exit(1)

    _log.info("starting_bot", log_level=config.log_level)

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
        _log.info("telegram_proxy_enabled")
    bot = Bot(token=config.bot_token, session=session)
    dp = Dispatcher()

    # Sentry error handler
    @dp.errors()
    async def sentry_error_handler(event):
        from aiogram.types import ErrorEvent
        if isinstance(event, ErrorEvent):
            capture_exception(event.exception)
            if _log:
                _log.error("unhandled_exception", error=str(event.exception))

    # Инжектим зависимости в хендлеры
    link.set_storage(storage)
    start.set_storage(storage)
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
    dp.include_router(cancel.router)
    dp.include_router(stats.router)
    dp.include_router(playlist.router)  # Последним — чтобы не перехватывал команды

    # Создаём событие для graceful shutdown
    _shutdown_event = asyncio.Event()

    # Настраиваем обработчики сигналов
    loop = asyncio.get_event_loop()

    def create_signal_handler(sig_name: str):
        def handler(sig, frame):
            asyncio.create_task(_shutdown(sig_name, scraper, cache_mgr, bot))
            if _shutdown_event:
                _shutdown_event.set()
        return handler

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, create_signal_handler(sig.name))
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler
            pass

    # Периодическая очистка кэша (каждые 6 часов)
    async def cache_cleanup_loop():
        while not (_shutdown_event and _shutdown_event.is_set()):
            try:
                await asyncio.sleep(6 * 3600)  # 6 часов
                if _shutdown_event and _shutdown_event.is_set():
                    break
                deleted = cache_mgr.cleanup()
                if deleted:
                    _log.info("scheduled_cache_cleanup", deleted=deleted)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log.error("cache_cleanup_error", error=str(e))

    cleanup_task = asyncio.create_task(cache_cleanup_loop())

    # Выбираем режим: webhook или polling
    if config.webhook_mode and config.webhook_url:
        _log.info("starting_webhook", url=config.webhook_url)

        # Удаляем старый webhook если есть
        await bot.delete_webhook(drop_pending_updates=True)

        # Устанавливаем новый webhook
        webhook_url = config.webhook_url.rstrip("/") + config.webhook_path
        await bot.set_webhook(
            url=webhook_url,
            secret_token=config.webhook_secret,
        )

        # Создаем aiohttp веб-сервер
        from aiohttp import web

        async def handle_webhook(request):
            """Обработчик входящих webhook запросов."""
            await dp.feed_webhook_update(bot, request.data)
            return web.Response(text="OK")

        app = web.Application()
        app.router.add_post(config.webhook_path, handle_webhook)

        # Запускаем веб-сервер
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=8080)
        await site.start()

        _log.info("webhook_server_started", port=8080, path=config.webhook_path)

        # Ждем сигнал shutdown
        try:
            await _shutdown_event.wait()
        finally:
            await runner.cleanup()

    else:
        # Обычный polling режим
        retry_delay = 5
        max_retry_delay = 60
        polling_active = True

        try:
            while polling_active:
                try:
                    me = await bot.me()
                    _log.info("bot_started", bot_id=me.id)
                    await dp.start_polling(bot)
                    break
                except (TelegramNetworkError, ProxyError) as e:
                    if _shutdown_event and _shutdown_event.is_set():
                        polling_active = False
                        break
                    _log.warning(
                        "telegram_network_error",
                        error=str(e),
                        retry_in_seconds=retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
    finally:
        # Останавливаем фоновые задачи
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

        # Очищаем ресурсы
        await _shutdown("polling_exit", scraper, cache_mgr, bot)


if __name__ == "__main__":
    asyncio.run(main())


