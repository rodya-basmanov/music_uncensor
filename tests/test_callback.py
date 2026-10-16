"""Тесты безопасных утилит (utils/callback.py, utils/safe.py) — без сети."""

from utils.callback import parse_cb, to_int
from utils.safe import is_silent


def test_parse_cb_basic():
    assert parse_cb("fix|abc", "fix|", 1) == ["abc"]
    assert parse_cb("pick|tok|3", "pick|", 2) == ["tok", "3"]
    assert parse_cb("albumpick|tok|2", "albumpick|", 2) == ["tok", "2"]
    assert parse_cb("export|txt|tsk", "export|", 2) == ["txt", "tsk"]
    assert parse_cb("noop", "noop", 0) == []


def test_parse_cb_rejects_bad():
    assert parse_cb(None, "fix|", 1) is None
    assert parse_cb("fix|abc|extra", "fix|", 1) is None
    assert parse_cb("redo|abc", "fix|", 1) is None
    assert parse_cb("fix|", "fix|", 1) is None


def test_to_int():
    assert to_int("3") == 3
    assert to_int("-2") == -2
    assert to_int("x") is None
    assert to_int(None) is None


def test_is_silent():
    assert is_silent(Exception("Bad Request: message is not modified"))
    assert is_silent(Exception("query is too old"))
    assert not is_silent(Exception("chat not found"))
