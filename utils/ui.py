"""UI-хелперы форматирования сообщений бота."""

import re

# Ключи-маркеры мешапов/версий — для них SoundCloud приоритетнее
_MASHUP_RE = re.compile(
    r"(?<![a-zа-я0-9])"
    r"(flip|mashup|mash|bootleg|vs|edit|remix|slowed|reverb|sped|instrumental)"
    r"(?![a-zа-я0-9])",
    re.IGNORECASE,
)

_LIVE_RE = re.compile(
    r"(?<![a-zа-я0-9])(live|concert|orchestral|acoustic)(?![a-zа-я0-9])",
    re.IGNORECASE,
)


def version_tag(title: str) -> str:
    """Значки версии трека для пикера: 🎬 live/концерт, 🔀 ремикс/слоу/мешап."""
    text = title or ""
    tag = ""
    if _LIVE_RE.search(text):
        tag += "🎬"
    if _MASHUP_RE.search(text):
        tag += "🔀"
    return tag


def progress_bar(done: int, total: int, length: int = 10,
                 filled: str = "🟩", empty: str = "⬜") -> str:
    """Визуальная полоса прогресса."""
    if total <= 0:
        return ""
    done = max(0, min(done, total))
    filled_n = int(done / total * length)
    pct = round(done / total * 100)
    return f"{filled * filled_n}{empty * (length - filled_n)} {pct}%"


def is_mashup(text: str) -> bool:
    """Похоже ли название на мешап/версию (искать лучше в SoundCloud)."""
    return bool(_MASHUP_RE.search(text or ""))


def source_icon(source: str) -> str:
    return {"youtube": "▶️", "soundcloud": "☁️"}.get(source, "🎵")


def fmt_duration_mmss(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "?"
    if s <= 0:
        return "?"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"
