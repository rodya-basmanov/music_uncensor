"""Тесты хранилища привязок (utils/storage.py)."""

from utils.storage import UserStorage


def test_storage_channels(tmp_path):
    s = UserStorage(str(tmp_path / "users.json"))
    uid = 123

    assert s.get_channels(uid) == []
    assert s.get_default_channel(uid) is None

    s.add_channel(uid, -1001234567890)
    assert s.has_channel(uid, -1001234567890) is True
    assert s.get_default_channel(uid) == -1001234567890

    s.add_channel(uid, -1009999999999)
    assert set(s.get_channels(uid)) == {-1001234567890, -1009999999999}
    # последний добавленный становится каналом по умолчанию
    assert s.get_default_channel(uid) == -1009999999999

    assert s.remove_channel(uid, -1001234567890) is True
    assert s.remove_channel(uid, -1001234567890) is False


def test_storage_rejects_invalid_channel(tmp_path):
    s = UserStorage(str(tmp_path / "users.json"))
    s.add_channel(1, 5)  # abs(channel_id) < 100 → отклоняется
    assert s.get_channels(1) == []


def test_storage_owner_is_first_linked(tmp_path):
    s = UserStorage(str(tmp_path / "users.json"))
    assert s.get_owner() is None

    s.add_channel(111, -1001111111111)
    s.add_channel(222, -1002222222222)
    assert s.get_owner() == 111

    s.remove_channel(111, -1001111111111)
    assert s.get_owner() == 222
