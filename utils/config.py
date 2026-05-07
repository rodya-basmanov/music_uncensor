"""Загрузка конфигурации из .env."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass
class Config:
    """Конфигурация приложения."""
    # Telegram
    bot_token: str = ""

    # VK
    vk_token: str = ""
    vk_user_id: int = 0

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


def load_config() -> Config:
    """Загружает конфигурацию из .env файла."""
    load_dotenv()

    return Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        vk_token=os.getenv("VK_TOKEN", ""),
        vk_user_id=int(os.getenv("VK_USER_ID", "0")),
        fuzzy_threshold=int(os.getenv("FUZZY_THRESHOLD", "85")),
        max_playlist_size=int(os.getenv("MAX_PLAYLIST_SIZE", "100")),
        cache_dir=os.getenv("CACHE_DIR", "./data/cache"),
        cache_ttl_days=int(os.getenv("CACHE_TTL_DAYS", "7")),
        max_cache_size_gb=int(os.getenv("MAX_CACHE_SIZE_GB", "20")),
        rate_limit_search=float(os.getenv("RATE_LIMIT_SEARCH_SEC", "2")),
        rate_limit_download=float(os.getenv("RATE_LIMIT_DOWNLOAD_SEC", "2")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
