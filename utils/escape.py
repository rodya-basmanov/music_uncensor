"""Экранирование пользовательских строк для HTML и rich-markdown.

Нужно, чтобы названия треков/плейлистов с символами <, &, *, _ и т.п. не ломали
разметку сообщений Telegram (parse_mode).
"""

import html as _html
import re

# Символы, ломающие inline-разметку в markdown: \ ` * _ [ ] < > ~
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>~])")


def esc_html(value) -> str:
    """Экранирует строку для parse_mode=HTML (без кавычек)."""
    return _html.escape(str(value if value is not None else ""), quote=False)


def esc_md(value) -> str:
    """Экранирует строку для rich-markdown (обратный слэш перед спецсимволами)."""
    return _MD_SPECIAL.sub(r"\\\1", str(value if value is not None else ""))
