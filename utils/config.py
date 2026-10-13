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
    telegram_proxy_required: bool = False

    # Sources
    vk_token: str = ""
    vk_playlist_mode: str = "ask"
    ytdlp_proxy: str = ""
    ytdlp_pot_provider: str = ""
    source_order: list[str] = field(default_factory=lambda: ["youtube", "soundcloud"])

    # Paths
    data_dir: str = "./data"

    # Playlist processing
    max_playlist_size: int = 500
    concurrency: int = 3
    send_interval: float = 3.0

    # Audio caption (song.link + via @bot)
    audio_caption: bool = True
    via_username: str = ""

    # Обложки: минимальный fuzzy-скор для VK-обложки (иначе — обложка источника)
    vk_cover_min_score: int = 90

    # Cache
    cache_dir: str = "./data/cache"
    cache_ttl_days: int = 7
    max_cache_size_gb: int = 20

    # Rate limit (seconds)
    rate_limit_search: float = 2.0

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

    telegram_proxy = os.getenv("TELEGRAM_PROXY_URL", "")
    source_order = [
        s.strip().lower()
        for s in os.getenv("SOURCE_ORDER", "youtube,soundcloud").split(",")
        if s.strip()
    ]

    return Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        data_dir=os.getenv("DATA_DIR", "./data"),
        telegram_proxy_url=telegram_proxy,
        telegram_proxy_required=_env_bool("TELEGRAM_PROXY_REQUIRED", False),

        vk_token=os.getenv("VK_TOKEN", ""),
        vk_playlist_mode=os.getenv("VK_PLAYLIST_MODE", "ask").strip().lower() or "ask",
        ytdlp_proxy=os.getenv("YTDLP_PROXY", "") or telegram_proxy,
        ytdlp_pot_provider=os.getenv("YTDLP_POT_PROVIDER", ""),
        source_order=source_order or ["youtube", "soundcloud"],

        max_playlist_size=int(os.getenv("MAX_PLAYLIST_SIZE", "500")),
        concurrency=max(1, int(os.getenv("CONCURRENCY", "3"))),
        send_interval=max(0.0, float(os.getenv("SEND_INTERVAL", "3"))),
        audio_caption=_env_bool("AUDIO_CAPTION", True),
        via_username=os.getenv("VIA_USERNAME", ""),
        vk_cover_min_score=int(os.getenv("VK_COVER_MIN_SCORE", "90")),
        cache_dir=os.getenv("CACHE_DIR", "./data/cache"),
        cache_ttl_days=int(os.getenv("CACHE_TTL_DAYS", "7")),
        max_cache_size_gb=int(os.getenv("MAX_CACHE_SIZE_GB", "20")),
        rate_limit_search=float(os.getenv("RATE_LIMIT_SEARCH_SEC", "2")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        sentry_dsn=os.getenv("SENTRY_DSN", ""),
        webhook_mode=_env_bool("WEBHOOK_MODE", False),
        webhook_url=os.getenv("WEBHOOK_URL", ""),
        webhook_path=os.getenv("WEBHOOK_PATH", "/webhook"),
        webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
    )
