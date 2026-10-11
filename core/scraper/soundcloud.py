"""Источник SoundCloud через yt-dlp (scsearch).

Работает в РФ без VPN. Часто содержит uncensored/оригинальные версии.
"""

from typing import List

from core.scraper.common import Candidate, parse_search_output, run_ytdlp
from utils.logger import get_logger

log = get_logger("soundcloud")

_PRINT = "%(webpage_url)s|||%(title)s|||%(duration)s|||%(uploader)s|||%(channel)s"


def search(query: str, count: int = 5, proxy: str = "") -> List[Candidate]:
    """Ищет треки на SoundCloud через yt-dlp flat search."""
    if not query:
        return []
    args = ["--flat-playlist", "--print", _PRINT, f"scsearch{count}:{query}"]
    if proxy:
        args += ["--proxy", proxy]
    try:
        result = run_ytdlp(args, timeout=45)
    except Exception as e:
        log.warning("soundcloud_search_error", query=query, error=str(e))
        return []

    candidates = parse_search_output(result.stdout, "soundcloud")
    log.info("soundcloud_search_done", query=query, count=len(candidates))
    return candidates
