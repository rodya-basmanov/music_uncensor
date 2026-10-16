"""Хранилище привязок user_id → channels на SQLite.

Публичный API совпадает с прежним JSON-хранилищем, но данные лежат в
`data/bot.db`. При первом запуске старый `users.json` (если есть) переносится.
"""

import json
import os
from typing import List, Optional

from utils.db import Database, get_db
from utils.logger import get_logger

log = get_logger("storage")


class UserStorage:
    """Хранилище привязок user_id → channels (SQLite)."""

    def __init__(self, filepath: str = "./data/users.json", db: Optional[Database] = None):
        self.filepath = filepath
        data_dir = os.path.dirname(filepath) or "."
        self.db = db or get_db(os.path.join(data_dir, "bot.db"))
        # in-memory слой над SQLite: горячие чтения (канал по умолчанию/привязки)
        self._cache: dict[int, dict] = {}
        self._migrate_json(filepath)

    def _user(self, user_id: int) -> dict:
        """Данные пользователя из памяти (ленивая загрузка из БД)."""
        c = self._cache.get(user_id)
        if c is None:
            c = {
                "channels": self.db.get_channels(user_id),
                "default": self.db.get_default_channel(user_id),
            }
            self._cache[user_id] = c
        return c

    def _migrate_json(self, filepath: str) -> None:
        """Переносит пользователей из старого users.json, если база ещё пустая."""
        if not os.path.exists(filepath):
            return
        try:
            if self.db.query_one("SELECT 1 FROM users LIMIT 1") is not None:
                return
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            for uid, info in data.items():
                try:
                    user_id = int(uid)
                except (TypeError, ValueError):
                    continue
                for ch in info.get("channels", []) or []:
                    self.db.add_channel(user_id, int(ch))
                if info.get("default_channel") and info["channels"]:
                    self.db.add_channel(user_id, int(info["default_channel"]))
                tasks = int(info.get("tasks_done", 0) or 0)
                if tasks:
                    for _ in range(tasks):
                        self.db.increment_tasks(user_id)
            log.info("storage_migrated_from_json", users=len(data))
        except Exception as e:
            log.warning("storage_migrate_failed", error=str(e))

    # ----------------------------- каналы --------------------------------------

    def add_channel(self, user_id: int, channel_id: int) -> None:
        """Привязывает канал к пользователю и делает его каналом по умолчанию."""
        if abs(channel_id) < 100:
            log.warning("invalid_channel_id_rejected", user_id=user_id, channel_id=channel_id)
            return
        self.db.add_channel(user_id, channel_id)
        self._cache.pop(user_id, None)
        log.info("channel_linked", user_id=user_id, channel_id=channel_id)

    def remove_channel(self, user_id: int, channel_id: int) -> bool:
        """Отвязывает канал. Возвращает True если канал был найден."""
        removed = self.db.remove_channel(user_id, channel_id)
        if removed:
            self._cache.pop(user_id, None)
            log.info("channel_unlinked", user_id=user_id, channel_id=channel_id)
        return removed

    def get_channels(self, user_id: int) -> List[int]:
        return list(self._user(user_id)["channels"])

    def has_channel(self, user_id: int, channel_id: int) -> bool:
        return channel_id in self._user(user_id)["channels"]

    def get_default_channel(self, user_id: int) -> Optional[int]:
        return self._user(user_id)["default"]

    def increment_tasks(self, user_id: int) -> None:
        self.db.ensure_user(user_id)
        self.db.increment_tasks(user_id)

    def get_owner(self) -> Optional[int]:
        """Владелец — первый пользователь, привязавший канал."""
        return self.db.get_owner()
