"""Точка входа бота — инициализация и запуск polling."""

import asyncio
import concurrent.futures
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sentry_sdk
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import BotCommand
from python_socks import ProxyError
from sentry_sdk import capture_exception

from bot.handlers import album, cancel, link, playlist, start, stats, status
from bot.handlers import health as health_handler
from core.cache_manager import CacheManager
from core.scraper.common import set_pot_provider
from services import health as health_service
from services import processor, tg_sender
from utils.config import load_config
from utils.logger import get_logger, setup_logging
from utils.net import proxy_reachable
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

    # Больше потоков для параллельных yt-dlp процессов (поиск/скачивание)
    asyncio.get_event_loop().set_default_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="vk"),
    )

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
    telegram_mode = "direct"
    if config.telegram_proxy_url:
        if proxy_reachable(config.telegram_proxy_url):
            session = AiohttpSession(proxy=config.telegram_proxy_url)
            telegram_mode = "proxy"
            _log.info("telegram_proxy_enabled")
        elif config.telegram_proxy_required:
            _log.error("telegram_proxy_unreachable_required", proxy=config.telegram_proxy_url)
            sys.exit(1)
        else:
            _log.warning(
                "telegram_proxy_unreachable_fallback_direct", proxy=config.telegram_proxy_url,
            )
    else:
        _log.info("telegram_direct")

    if config.ytdlp_proxy:
        if proxy_reachable(config.ytdlp_proxy):
            _log.info("ytdlp_proxy_enabled", proxy=config.ytdlp_proxy)
        else:
            _log.warning("ytdlp_proxy_unreachable", proxy=config.ytdlp_proxy)

    health_service.set_telegram_mode(telegram_mode, config.telegram_proxy_url)

    bot = Bot(token=config.bot_token, session=session)
    dp = Dispatcher()

    health_service.mark_start()

    async def _send_dm(uid: int, text: str) -> bool:
        await bot.send_message(uid, text, parse_mode="HTML")
        return True

    health_service.configure(storage.get_owner, _send_dm)

    @dp.error()
    async def sentry_error_handler(event):
        from aiogram.types import ErrorEvent
        if not isinstance(event, ErrorEvent):
            return
        exc = event.exception
        if isinstance(exc, TelegramBadRequest):
            text = str(exc).lower()
            if ("query is too old" in text or "query id is invalid" in text
                    or "message is not modified" in text):
                if _log:
                    _log.debug("benign_bad_request", error=str(exc))
                return
        capture_exception(exc)
        if _log:
            _log.error("unhandled_exception", error=str(exc))
        asyncio.create_task(health_service.alert_owner(
            f"Необработанное исключение:\n<code>{str(exc)[:300]}</code>",
            key="unhandled",
        ))

    start.set_storage(storage)
    link.set_storage(storage)
    processor.set_vk_token(config.vk_token)
    processor.set_stats_path(os.path.join(config.data_dir, "stats.json"))
    processor.set_exports_dir(os.path.join(config.data_dir, "exports"))
    processor.set_caption(config.audio_caption)
    processor.set_vk_cover_min_score(config.vk_cover_min_score)
    tg_sender.set_send_interval(config.send_interval)
    set_pot_provider(config.ytdlp_pot_provider)
    if config.ytdlp_pot_provider:
        _log.info("pot_provider_enabled", url=config.ytdlp_pot_provider)
    asyncio.create_task(processor.warm_vk())
    try:
        me = await bot.me()
        tg_sender.set_bot_username(config.via_username or (me.username or ""))
    except Exception as e:
        _log.warning("bot_me_failed", error=str(e))
    playlist.set_dependencies(
        cache_mgr=cache_mgr, storage=storage,
        max_playlist_size=config.max_playlist_size, rate_limit=config.rate_limit_search,
        source_order=config.source_order, ytdlp_proxy=config.ytdlp_proxy, vk_token=config.vk_token,
        vk_playlist_mode=config.vk_playlist_mode, concurrency=config.concurrency,
    )
    health_handler.set_dependencies(
        cache_mgr=cache_mgr, storage=storage,
        ytdlp_proxy=config.ytdlp_proxy, pot_provider=config.ytdlp_pot_provider,
        vk_token=config.vk_token,
    )
    album.set_dependencies(
        storage=storage, ytdlp_proxy=config.ytdlp_proxy, vk_token=config.vk_token,
    )

    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Меню"),
            BotCommand(command="help", description="Справка"),
            BotCommand(command="link", description="Привязать канал"),
            BotCommand(command="channels", description="Мои каналы"),
            BotCommand(command="unlink", description="Отвязать канал"),
            BotCommand(command="status", description="Статус задачи"),
            BotCommand(command="stats", description="Статистика"),
            BotCommand(command="album", description="Скачать альбом"),
            BotCommand(command="health", description="Состояние бота"),
            BotCommand(command="cancel", description="Отменить задачу"),
        ])
        _log.info("commands_registered")
    except Exception as e:
        _log.warning("set_my_commands_failed", error=str(e))

    dp.include_router(start.router)
    dp.include_router(link.router)
    dp.include_router(status.router)
    dp.include_router(cancel.router)
    dp.include_router(stats.router)
    dp.include_router(health_handler.router)
    dp.include_router(album.router)
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

    async def heartbeat_loop():
        while not (_shutdown_event and _shutdown_event.is_set()):
            health_service.beat()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break

    heartbeat_task = asyncio.create_task(heartbeat_loop())

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
        using_proxy = telegram_mode == "proxy"
        try:
            while polling_active:
                try:
                    if (
                        not using_proxy
                        and config.telegram_proxy_url
                        and proxy_reachable(config.telegram_proxy_url)
                    ):
                        old_session = bot.session
                        bot.session = AiohttpSession(proxy=config.telegram_proxy_url)
                        using_proxy = True
                        health_service.set_telegram_mode("proxy", config.telegram_proxy_url)
                        try:
                            await old_session.close()
                        except Exception:
                            pass
                        _log.info("telegram_proxy_recovered", proxy=config.telegram_proxy_url)
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
            heartbeat_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await _shutdown("polling_exit", cache_mgr, bot)


if __name__ == "__main__":
    asyncio.run(main())
