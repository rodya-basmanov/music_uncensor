"""Чтение/запись users.json с кэшем в памяти (write-through)."""

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

log = get_logger("storage")


class UserStorage:
    """Хранилище привязок user_id → channels.

    Данные держатся в памяти (один процесс/loop), на диск пишем при изменениях —
    это убирает синхронное чтение файла на каждое сообщение.
    """

    def __init__(self, filepath: str = "./data/users.json"):
        self.filepath = filepath
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        self._data: Dict[str, Any] = self._load()
        if not os.path.exists(filepath):
            self._persist(self._data)

    def _load(self) -> Dict[str, Any]:
        """Читает JSON-файл один раз при старте."""
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
        except OSError as e:
            log.warning("storage_read_error", error=str(e))
            return {}

    def _read(self) -> Dict[str, Any]:
        return self._data

    def _write(self, data: Dict[str, Any]) -> None:
        """Обновляет память и сохраняет на диск."""
        self._data = data
        self._persist(data)

    def _persist(self, data: Dict[str, Any]) -> None:
        """Атомарная запись через temp-файл + replace."""
        dir_name = os.path.dirname(self.filepath) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.filepath)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def add_channel(self, user_id: int, channel_id: int) -> None:
        """Привязывает канал к пользователю и делает его каналом по умолчанию."""
        if abs(channel_id) < 100:
            log.warning("invalid_channel_id_rejected", user_id=user_id, channel_id=channel_id)
            return
        data = self._read()
        uid = str(user_id)
        if uid not in data:
            data[uid] = {"channels": [], "last_active": 0, "tasks_done": 0}
        if channel_id not in data[uid]["channels"]:
            data[uid]["channels"].append(channel_id)
        data[uid]["default_channel"] = channel_id
        data[uid]["last_active"] = int(time.time())
        self._write(data)
        log.info("channel_linked", user_id=user_id, channel_id=channel_id)

    def remove_channel(self, user_id: int, channel_id: int) -> bool:
        """Отвязывает канал. Возвращает True если канал был найден."""
        data = self._read()
        uid = str(user_id)
        if uid in data and channel_id in data[uid]["channels"]:
            data[uid]["channels"].remove(channel_id)
            if data[uid].get("default_channel") == channel_id:
                remaining = data[uid]["channels"]
                if remaining:
                    data[uid]["default_channel"] = remaining[0]
                else:
                    data[uid].pop("default_channel", None)
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

    def get_default_channel(self, user_id: int) -> Optional[int]:
        """Возвращает канал по умолчанию или первый из списка."""
        data = self._read()
        uid = str(user_id)
        if uid in data:
            default = data[uid].get("default_channel")
            if default is not None:
                return default
            channels = data[uid].get("channels", [])
            if channels:
                return channels[0]
        return None

    def increment_tasks(self, user_id: int) -> None:
        """Увеличивает счётчик выполненных задач."""
        data = self._read()
        uid = str(user_id)
        if uid in data:
            data[uid]["tasks_done"] = data[uid].get("tasks_done", 0) + 1
            data[uid]["last_active"] = int(time.time())
            self._write(data)

    def get_owner(self) -> Optional[int]:
        """Владелец — первый пользователь, привязавший канал."""
        data = self._read()
        for uid, info in data.items():
            if info.get("channels"):
                try:
                    return int(uid)
                except (TypeError, ValueError):
                    continue
        return None
