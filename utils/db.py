"""SQLite-хранилище: пользователи, каналы, статистика, история, избранное.

Один файл (`data/bot.db`) вместо россыпи JSON. Операции маленькие и синхронные
(sqlite3 под потоковым локом), поэтому вызываются прямо из event loop — это
доли миллисекунды, без внешних зависимостей.
"""

import json
import os
import sqlite3
import threading
import time
from typing import Any, List, Optional

from utils.logger import get_logger

log = get_logger("db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY,
    default_channel INTEGER,
    last_active     INTEGER DEFAULT 0,
    tasks_done      INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS channels (
    user_id    INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, channel_id)
);
CREATE TABLE IF NOT EXISTS stats (
    key   TEXT PRIMARY KEY,
    value INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    artist     TEXT DEFAULT '',
    title      TEXT DEFAULT '',
    source     TEXT DEFAULT '',
    channel_id INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id DESC);
CREATE TABLE IF NOT EXISTS favorites (
    user_id    INTEGER NOT NULL,
    artist     TEXT NOT NULL,
    title      TEXT NOT NULL,
    created_at INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, artist, title)
);
CREATE TABLE IF NOT EXISTS kv_cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    expires_at INTEGER DEFAULT 0
);
"""

_HISTORY_KEEP = 200  # на пользователя


class Database:
    """Тонкая синхронная обёртка над sqlite3 (потокобезопасная)."""

    def __init__(self, path: str = "./data/bot.db"):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.commit()

    # ----------------------------- низкий уровень -----------------------------

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----------------------------- пользователи/каналы -------------------------

    def ensure_user(self, user_id: int) -> None:
        self.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))

    def add_channel(self, user_id: int, channel_id: int) -> None:
        self.ensure_user(user_id)
        self.execute("INSERT OR IGNORE INTO channels(user_id, channel_id) VALUES (?, ?)", (user_id, channel_id))
        self.execute(
            "UPDATE users SET default_channel=?, last_active=? WHERE user_id=?",
            (channel_id, int(time.time()), user_id),
        )

    def remove_channel(self, user_id: int, channel_id: int) -> bool:
        row = self.query_one(
            "SELECT 1 FROM channels WHERE user_id=? AND channel_id=?", (user_id, channel_id)
        )
        if row is None:
            return False
        self.execute("DELETE FROM channels WHERE user_id=? AND channel_id=?", (user_id, channel_id))
        default = self.query_one("SELECT default_channel FROM users WHERE user_id=?", (user_id,))
        if default and default["default_channel"] == channel_id:
            rest = self.query("SELECT channel_id FROM channels WHERE user_id=? ORDER BY channel_id", (user_id,))
            new_default = rest[0]["channel_id"] if rest else None
            self.execute("UPDATE users SET default_channel=? WHERE user_id=?", (new_default, user_id))
        return True

    def get_channels(self, user_id: int) -> List[int]:
        return [r["channel_id"] for r in self.query(
            "SELECT channel_id FROM channels WHERE user_id=? ORDER BY channel_id", (user_id,)
        )]

    def has_channel(self, user_id: int, channel_id: int) -> bool:
        return self.query_one(
            "SELECT 1 FROM channels WHERE user_id=? AND channel_id=?", (user_id, channel_id)
        ) is not None

    def get_default_channel(self, user_id: int) -> Optional[int]:
        row = self.query_one("SELECT default_channel FROM users WHERE user_id=?", (user_id,))
        if row and row["default_channel"] is not None:
            return row["default_channel"]
        channels = self.get_channels(user_id)
        return channels[0] if channels else None

    def increment_tasks(self, user_id: int) -> None:
        self.execute(
            "UPDATE users SET tasks_done=tasks_done+1, last_active=? WHERE user_id=?",
            (int(time.time()), user_id),
        )

    def get_owner(self) -> Optional[int]:
        row = self.query_one("SELECT user_id FROM channels ORDER BY rowid LIMIT 1")
        return int(row["user_id"]) if row else None

    def get_user(self, user_id: int) -> Optional[dict]:
        row = self.query_one("SELECT * FROM users WHERE user_id=?", (user_id,))
        return dict(row) if row else None

    # ----------------------------- статистика ----------------------------------

    def set_stats(self, stats: dict) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT INTO stats(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(k, int(v)) for k, v in stats.items()],
            )
            self._conn.commit()

    def get_stats(self) -> dict:
        return {r["key"]: r["value"] for r in self.query("SELECT key, value FROM stats")}

    # ----------------------------- история -------------------------------------

    def add_history(self, user_id: int, artist: str, title: str, source: str = "",
                    channel_id: int = 0) -> None:
        self.execute(
            "INSERT INTO history(user_id, artist, title, source, channel_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, artist or "", title or "", source or "", channel_id or 0, int(time.time())),
        )
        # держим историю на пользователя ограниченной
        self.execute(
            "DELETE FROM history WHERE user_id=? AND id NOT IN "
            "(SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?)",
            (user_id, user_id, _HISTORY_KEEP),
        )

    def get_history(self, user_id: int, limit: int = 10) -> List[dict]:
        return [dict(r) for r in self.query(
            "SELECT artist, title, source, created_at FROM history "
            "WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, max(1, int(limit))),
        )]

    def get_my_artists(self, user_id: int, limit: int = 10) -> List[dict]:
        """Артисты пользователя по частоте загрузок."""
        return [dict(r) for r in self.query(
            "SELECT artist, COUNT(*) AS cnt FROM history "
            "WHERE user_id=? AND artist<>'' GROUP BY lower(artist) "
            "ORDER BY cnt DESC, artist LIMIT ?",
            (user_id, max(1, int(limit))),
        )]

    # ----------------------------- избранное -----------------------------------

    def add_favorite(self, user_id: int, artist: str, title: str) -> None:
        self.execute(
            "INSERT OR IGNORE INTO favorites(user_id, artist, title, created_at) VALUES (?, ?, ?, ?)",
            (user_id, artist or "", title or "", int(time.time())),
        )

    def remove_favorite(self, user_id: int, artist: str, title: str) -> None:
        self.execute(
            "DELETE FROM favorites WHERE user_id=? AND artist=? AND title=?", (user_id, artist, title)
        )

    def is_favorite(self, user_id: int, artist: str, title: str) -> bool:
        return self.query_one(
            "SELECT 1 FROM favorites WHERE user_id=? AND artist=? AND title=?", (user_id, artist, title)
        ) is not None

    def list_favorites(self, user_id: int) -> List[dict]:
        return [dict(r) for r in self.query(
            "SELECT artist, title FROM favorites WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        )]

    # ----------------------------- persistent cache ----------------------------

    def cache_get(self, key: str) -> Optional[Any]:
        """Читает значение KV-кэша (None, если нет/истёк)."""
        row = self.query_one("SELECT payload, expires_at FROM kv_cache WHERE key=?", (key,))
        if row is None:
            return None
        if row["expires_at"] and row["expires_at"] < time.time():
            self.execute("DELETE FROM kv_cache WHERE key=?", (key,))
            return None
        try:
            return json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            return None

    def cache_set(self, key: str, value: Any, ttl: float) -> None:
        """Пишет значение KV-кэша с TTL (секунды)."""
        payload = json.dumps(value, ensure_ascii=False)
        self.execute(
            "INSERT INTO kv_cache(key, payload, expires_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, expires_at=excluded.expires_at",
            (key, payload, int(time.time() + ttl)),
        )

    def cache_prune(self) -> int:
        """Удаляет просроченные записи. Возвращает число удалённых."""
        cur = self.execute("DELETE FROM kv_cache WHERE expires_at>0 AND expires_at<?", (int(time.time()),))
        return cur.rowcount


_dbs: dict[str, Database] = {}


def get_db(path: str = "./data/bot.db") -> Database:
    """Ленивый синглтон базы (по одному соединению на путь)."""
    key = os.path.abspath(path)
    db = _dbs.get(key)
    if db is None:
        db = Database(path)
        _dbs[key] = db
    return db
