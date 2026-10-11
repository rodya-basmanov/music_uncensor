"""Источник YouTube через yt-dlp (ytsearch).

В РФ YouTube блокируется — поиск/скачивание идут через прокси (YTDLP_PROXY).
"""

from typing import List

from core.scraper.common import Candidate, parse_search_output, run_ytdlp
from utils.logger import get_logger

log = get_logger("youtube")

_PRINT = "%(webpage_url)s|||%(title)s|||%(duration)s|||%(uploader)s|||%(channel)s"


def search(query: str, count: int = 5, proxy: str = "") -> List[Candidate]:
    """Ищет треки в YouTube (ytsearch)."""
    if not query:
        return []
    args = ["--flat-playlist", "--print", _PRINT, f"ytsearch{count}:{query}"]
    if proxy:
        args += ["--proxy", proxy]
    try:
        result = run_ytdlp(args, timeout=45)
    except Exception as e:
        log.warning("youtube_search_error", query=query, error=str(e))
        return []

    candidates = parse_search_output(result.stdout, "youtube")
    norm = []
    for c in candidates:
        if not c.url.startswith("http"):
            c.url = f"https://www.youtube.com/watch?v={c.url}"
        norm.append(c)
    log.info("youtube_search_done", query=query, count=len(norm))
    return norm
