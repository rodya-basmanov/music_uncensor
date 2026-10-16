"""Тесты SQLite-хранилища (utils/db.py) и миграции users.json."""

import json

from utils.db import Database
from utils.storage import UserStorage


def _db(tmp_path):
    return Database(str(tmp_path / "bot.db"))


def test_channels_and_default(tmp_path):
    db = _db(tmp_path)
    assert db.get_channels(5) == []
    assert db.get_default_channel(5) is None

    db.add_channel(5, -1001111111111)
    db.add_channel(5, -1002222222222)
    assert set(db.get_channels(5)) == {-1001111111111, -1002222222222}
    assert db.get_default_channel(5) == -1002222222222

    assert db.remove_channel(5, -1001111111111) is True
    assert db.remove_channel(5, -1001111111111) is False
    assert db.get_default_channel(5) == -1002222222222


def test_owner_is_first_linked(tmp_path):
    db = _db(tmp_path)
    assert db.get_owner() is None
    db.add_channel(111, -1001111111111)
    db.add_channel(222, -1002222222222)
    assert db.get_owner() == 111
    db.remove_channel(111, -1001111111111)
    assert db.get_owner() == 222


def test_stats_roundtrip(tmp_path):
    db = _db(tmp_path)
    db.set_stats({"total_sent": 3, "total_cached": 1})
    assert db.get_stats() == {"total_sent": 3, "total_cached": 1}
    db.set_stats({"total_sent": 10})
    assert db.get_stats()["total_sent"] == 10


def test_history_and_artists(tmp_path):
    db = _db(tmp_path)
    for i in range(3):
        db.add_history(7, "SALUKI", f"track{i}", "vk", -100)
    db.add_history(7, "MARKUL", "last", "youtube", -100)
    hist = db.get_history(7, 10)
    assert hist[0]["title"] == "last"
    assert len(hist) == 4
    artists = db.get_my_artists(7, 5)
    assert artists[0]["artist"] == "SALUKI" and artists[0]["cnt"] == 3


def test_history_is_per_user_and_limited(tmp_path):
    db = _db(tmp_path)
    for i in range(250):
        db.add_history(1, "A", f"t{i}")
    db.add_history(2, "B", "only")
    assert len(db.get_history(1, 1000)) <= 200
    assert db.get_history(2, 10)[0]["title"] == "only"


def test_favorites(tmp_path):
    db = _db(tmp_path)
    assert db.is_favorite(1, "SALUKI", "EUPHORIA") is False
    db.add_favorite(1, "SALUKI", "EUPHORIA")
    assert db.is_favorite(1, "SALUKI", "EUPHORIA") is True
    assert db.list_favorites(1) == [{"artist": "SALUKI", "title": "EUPHORIA"}]
    db.remove_favorite(1, "SALUKI", "EUPHORIA")
    assert db.list_favorites(1) == []


def test_kv_cache_roundtrip_and_expiry(tmp_path):
    db = _db(tmp_path)
    assert db.cache_get("missing") is None
    db.cache_set("x", {"a": [1, 2, 3], "b": "тест"}, 60)
    assert db.cache_get("x") == {"a": [1, 2, 3], "b": "тест"}
    db.cache_set("y", "v", -1)  # уже просрочен
    assert db.cache_get("y") is None


def test_storage_migrates_from_json(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(json.dumps({
        "111": {"channels": [-1001111111111], "default_channel": -1001111111111, "tasks_done": 2},
        "222": {"channels": [-1002222222222]},
    }), encoding="utf-8")
    s = UserStorage(str(path))
    assert s.get_channels(111) == [-1001111111111]
    assert s.get_default_channel(111) == -1001111111111
    assert s.get_owner() == 111
    assert s.get_channels(222) == [-1002222222222]
