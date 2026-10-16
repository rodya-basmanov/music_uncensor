"""Единое TTL/LRU-хранилище для интерактивных сессий (токены, пикеры и т.п.).

Заменяет россыпь глобальных словарей с ручной уборкой: TTL и лимит размера
применяются автоматически, плюс есть общий фоновой свипер.
"""

import asyncio
import time
from typing import Any, Dict, List, Tuple

_STORES: List["PendingStore"] = []


class PendingStore:
    """Словарь с TTL (по полю created) и опциональным лимитом размера."""

    def __init__(self, ttl: float, maxsize: int = 0, touch: bool = False, reg: bool = True):
        self.ttl = ttl
        self.maxsize = maxsize
        self.touch = touch  # продлевать TTL при обращении
        self._data: Dict[Any, Any] = {}
        if reg:
            _STORES.append(self)

    def _created(self, item: Any) -> float:
        return item.get("created", 0.0) if isinstance(item, dict) else 0.0

    def _expired(self, item: Any) -> bool:
        created = self._created(item)
        return created > 0 and (time.time() - created) > self.ttl

    def sweep(self) -> int:
        removed = 0
        for k in [k for k, v in self._data.items() if self._expired(v)]:
            self._data.pop(k, None)
            removed += 1
        if self.maxsize and len(self._data) > self.maxsize:
            order = sorted(self._data.items(), key=lambda kv: self._created(kv[1]))
            for k, _ in order[: len(self._data) - self.maxsize]:
                self._data.pop(k, None)
                removed += 1
        return removed

    def __setitem__(self, key: Any, value: Any) -> None:
        if isinstance(value, dict):
            value.setdefault("created", time.time())
        self._data[key] = value
        self.sweep()

    def __getitem__(self, key: Any) -> Any:
        item = self._data[key]
        if self._expired(item):
            self._data.pop(key, None)
            raise KeyError(key)
        if self.touch and isinstance(item, dict):
            item["created"] = time.time()
        return item

    def get(self, key: Any, default: Any = None) -> Any:
        item = self._data.get(key)
        if item is None:
            return default
        if self._expired(item):
            self._data.pop(key, None)
            return default
        if self.touch and isinstance(item, dict):
            item["created"] = time.time()
        return item

    def pop(self, key: Any, default: Any = None) -> Any:
        item = self._data.pop(key, None)
        if item is None or self._expired(item):
            return default
        return item

    def setdefault(self, key: Any, default: Any) -> Any:
        item = self._data.get(key)
        if item is not None and not self._expired(item):
            return item
        if isinstance(default, dict):
            default.setdefault("created", time.time())
        self._data[key] = default
        return default

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        return len(self._data)

    def keys(self) -> List[Any]:
        self.sweep()
        return list(self._data.keys())

    def values(self) -> List[Any]:
        self.sweep()
        return list(self._data.values())

    def items(self) -> List[Tuple[Any, Any]]:
        self.sweep()
        return list(self._data.items())

    def clear(self) -> None:
        self._data.clear()


def sweep_all() -> int:
    """Убирает просроченные записи во всех зарегистрированных хранилищах."""
    return sum(s.sweep() for s in _STORES)


async def sweeper_loop(interval: float = 300.0) -> None:
    """Фоновой свипер: чистит все PendingStore раз в interval секунд."""
    while True:
        try:
            await asyncio.sleep(interval)
            sweep_all()
        except asyncio.CancelledError:
            return
        except Exception:
            pass
