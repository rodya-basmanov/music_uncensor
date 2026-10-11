"""Точка входа бота — инициализация и запуск polling."""

import asyncio
import signal
import sys
import os

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
from services import processor, tg_sender
from utils.config import load_config
from utils.logger import setup_logging, get_logger
from utils.storage import UserStorage

_shutdown_event: asyncio.Event | None = None
_log = None


async def _shutdown(sig_name: str, cache_mgr: CacheManager, bot: Bot):
    global _log
    if _log:
        _log.info("shutdown_initiated", signal=sig_name)
    if _shutdown_event:
        _shutdown_event.set()
    try:
        deleted = await cache_mgr.cleanup()
        if deleted and _log:
            _log.info("cache_cleanup_on_shutdown", deleted=deleted)
    except Exception as e:
        if _log:
            _log.error("cache_cleanup_error", error=str(e))
    try:
        await bot.session.close()
    except Exception:
        pass
    if _log:
        _log.info("shutdown_complete")


async def main():
    global _shutdown_event, _log

    config = load_config()
    setup_logging(config.log_level)
    _log = get_logger("main")

    if config.sentry_dsn:
        sentry_sdk.init(dsn=config.sentry_dsn, traces_sample_rate=0.1, environment="production")
        _log.info("sentry_enabled")

    if not config.bot_token:
        _log.error("BOT_TOKEN не задан в .env!")
        sys.exit(1)

    _log.info("starting_bot", log_level=config.log_level)

    storage = UserStorage(os.path.join(config.data_dir, "users.json"))
    cache_mgr = CacheManager(cache_dir=config.cache_dir, ttl_days=config.cache_ttl_days, max_size_gb=config.max_cache_size_gb)

    session = None
    if config.telegram_proxy_url:
        session = AiohttpSession(proxy=config.telegram_proxy_url)
        _log.info("telegram_proxy_enabled")
    bot = Bot(token=config.bot_token, session=session)
    dp = Dispatcher()

    @dp.error()
    async def sentry_error_handler(event):
        from aiogram.types import ErrorEvent
        if isinstance(event, ErrorEvent):
            capture_exception(event.exception)
            if _log:
                _log.error("unhandled_exception", error=str(event.exception))

    start.set_storage(storage)
    link.set_storage(storage)
    processor.set_vk_token(config.vk_token)
    tg_sender.set_send_interval(config.send_interval)
    playlist.set_dependencies(
        cache_mgr=cache_mgr, storage=storage,
        max_playlist_size=config.max_playlist_size, rate_limit=config.rate_limit_search,
        source_order=config.source_order, ytdlp_proxy=config.ytdlp_proxy, vk_token=config.vk_token,
        vk_playlist_mode=config.vk_playlist_mode, concurrency=config.concurrency,
    )

    dp.include_router(start.router)
    dp.include_router(link.router)
    dp.include_router(status.router)
    dp.include_router(cancel.router)
    dp.include_router(stats.router)
    dp.include_router(playlist.router)

    _shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    def create_signal_handler(sig_name: str):
        def handler(sig, frame):
            asyncio.create_task(_shutdown(sig_name, cache_mgr, bot))
        return handler

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, create_signal_handler(sig.name))
        except NotImplementedError:
            pass

    async def cache_cleanup_loop():
        while not (_shutdown_event and _shutdown_event.is_set()):
            try:
                await asyncio.sleep(6 * 3600)
                if _shutdown_event and _shutdown_event.is_set():
                    break
                deleted = await cache_mgr.cleanup()
                if deleted:
                    _log.info("scheduled_cache_cleanup", deleted=deleted)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log.error("cache_cleanup_error", error=str(e))

    cleanup_task = asyncio.create_task(cache_cleanup_loop())

    if config.webhook_mode and config.webhook_url:
        _log.info("starting_webhook", url=config.webhook_url)
        await bot.delete_webhook(drop_pending_updates=True)
        webhook_url = config.webhook_url.rstrip("/") + config.webhook_path
        await bot.set_webhook(url=webhook_url, secret_token=config.webhook_secret)
        from aiohttp import web
        async def handle_webhook(request):
            try:
                update = await request.json()
                await dp.feed_webhook_update(bot, update)
                return web.Response(text="OK")
            except Exception as e:
                _log.error("webhook_handle_error", error=str(e))
                return web.Response(text="Error", status=500)
        app = web.Application()
        app.router.add_post(config.webhook_path, handle_webhook)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=8080)
        await site.start()
        _log.info("webhook_server_started", port=8080, path=config.webhook_path)
        try:
            await _shutdown_event.wait()
        finally:
            await runner.cleanup()
    else:
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
                except (TelegramNetworkError, ProxyError, asyncio.IncompleteReadError, OSError) as e:
                    if _shutdown_event and _shutdown_event.is_set():
                        polling_active = False
                        break
                    _log.warning("telegram_network_error", error=str(e), retry_in_seconds=retry_delay)
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
        finally:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            await _shutdown("polling_exit", cache_mgr, bot)


if __name__ == "__main__":
    asyncio.run(main())