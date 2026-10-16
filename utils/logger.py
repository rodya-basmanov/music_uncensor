"""Настройка логирования через structlog."""

import logging
import re
from typing import Any, MutableMapping

import structlog

# логин:пароль в URL (прокси и т.п.) — маскируем в логах
_SECRET_RE = re.compile(r"://[^/@\s]+@")


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET_RE.sub("://***@", value)
    return value


def redact_secrets(logger, method_name, event_dict: MutableMapping[str, Any]):
    """structlog-процессор: прячет креды в URL по всем полям события."""
    for k, v in list(event_dict.items()):
        event_dict[k] = _redact(v)
    return event_dict


def setup_logging(log_level: str = "INFO") -> None:
    """Инициализирует structlog + стандартный logging."""
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_secrets,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = __name__):
    """Возвращает structlog logger."""
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    """Привязывает контекст (task_id, user_id, ...) ко всем логам в текущей задаче."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
