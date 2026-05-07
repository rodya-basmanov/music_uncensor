"""Атомарное чтение/запись users.json."""

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

log = get_logger("storage")


class UserStorage:
    """Хранилище привязок user_id → channels в JSON-файле."""

    def __init__(self, filepath: str = "./data/users.json"):
        self.filepath = filepath
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        if not os.path.exists(filepath):
            self._write({})

    def _read(self) -> Dict[str, Any]:
        """Читает JSON-файл."""
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write(self, data: Dict[str, Any]) -> None:
        """Атомарная запись через temp-файл + rename."""
        dir_name = os.path.dirname(self.filepath)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # На Windows нужно удалить целевой файл перед rename
            if os.path.exists(self.filepath):
                os.replace(tmp_path, self.filepath)
            else:
                os.rename(tmp_path, self.filepath)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def add_channel(self, user_id: int, channel_id: int) -> None:
        """Привязывает канал к пользователю."""
        data = self._read()
        uid = str(user_id)
        if uid not in data:
            data[uid] = {"channels": [], "last_active": 0, "tasks_done": 0}
        if channel_id not in data[uid]["channels"]:
            data[uid]["channels"].append(channel_id)
        data[uid]["last_active"] = int(time.time())
        self._write(data)
        log.info("channel_linked", user_id=user_id, channel_id=channel_id)

    def remove_channel(self, user_id: int, channel_id: int) -> bool:
        """Отвязывает канал. Возвращает True если канал был найден."""
        data = self._read()
        uid = str(user_id)
        if uid in data and channel_id in data[uid]["channels"]:
            data[uid]["channels"].remove(channel_id)
            self._write(data)
            log.info("channel_unlinked", user_id=user_id, channel_id=channel_id)
            return True
        return False

    def get_channels(self, user_id: int) -> List[int]:
        """Возвращает список привязанных каналов."""
        data = self._read()
        uid = str(user_id)
        if uid in data:
            return data[uid].get("channels", [])
        return []

    def has_channel(self, user_id: int, channel_id: int) -> bool:
        """Проверяет, привязан ли канал к пользователю."""
        return channel_id in self.get_channels(user_id)

    def increment_tasks(self, user_id: int) -> None:
        """Увеличивает счётчик выполненных задач."""
        data = self._read()
        uid = str(user_id)
        if uid in data:
            data[uid]["tasks_done"] = data[uid].get("tasks_done", 0) + 1
            data[uid]["last_active"] = int(time.time())
            self._write(data)
