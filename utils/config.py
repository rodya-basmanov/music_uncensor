"""Загрузка конфигурации из .env."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    """Конфигурация приложения."""
    # Telegram
    bot_token: str = ""
    telegram_proxy_url: str = ""

    # Matching
    fuzzy_threshold: int = 85
    max_playlist_size: int = 100

    # Cache
    cache_dir: str = "./data/cache"
    cache_ttl_days: int = 7
    max_cache_size_gb: int = 20

    # Rate limits (seconds)
    rate_limit_search: float = 2.0
    rate_limit_download: float = 2.0

    # Logging
    log_level: str = "INFO"

    # Sentry
    sentry_dsn: str = ""

    # Webhook (альтернатива polling)
    webhook_mode: bool = False
    webhook_url: str = ""
    webhook_path: str = "/webhook"
    webhook_secret: str = ""


def load_config() -> Config:
    """Загружает конфигурацию из .env файла."""
    load_dotenv()

    return Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        telegram_proxy_url=os.getenv("TELEGRAM_PROXY_URL", ""),
        fuzzy_threshold=int(os.getenv("FUZZY_THRESHOLD", "85")),
        max_playlist_size=int(os.getenv("MAX_PLAYLIST_SIZE", "100")),
        cache_dir=os.getenv("CACHE_DIR", "./data/cache"),
        cache_ttl_days=int(os.getenv("CACHE_TTL_DAYS", "7")),
        max_cache_size_gb=int(os.getenv("MAX_CACHE_SIZE_GB", "20")),
        rate_limit_search=float(os.getenv("RATE_LIMIT_SEARCH_SEC", "2")),
        rate_limit_download=float(os.getenv("RATE_LIMIT_DOWNLOAD_SEC", "2")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        sentry_dsn=os.getenv("SENTRY_DSN", ""),
        webhook_mode=_env_bool("WEBHOOK_MODE", False),
        webhook_url=os.getenv("WEBHOOK_URL", ""),
        webhook_path=os.getenv("WEBHOOK_PATH", "/webhook"),
        webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
    )
