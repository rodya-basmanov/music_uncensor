"""Тесты единого хранилища сессий (utils/pending.py) — без сети."""

import time

from utils.pending import PendingStore, sweep_all


def test_ttl_expiry():
    s = PendingStore(ttl=0.05, reg=False)
    s["a"] = {"x": 1}
    assert s.get("a")["x"] == 1
    time.sleep(0.1)
    assert s.get("a") is None
    assert "a" not in s
    assert len(s) == 0


def test_created_is_set():
    s = PendingStore(ttl=100, reg=False)
    s["a"] = {}
    assert "created" in s["a"]


def test_maxsize_evicts_oldest():
    s = PendingStore(ttl=100, maxsize=2, reg=False)
    now = time.time()
    s["a"] = {"created": now - 3}
    s["b"] = {"created": now - 2}
    s["c"] = {"created": now - 1}
    assert len(s) <= 2
    assert s.get("a") is None
    assert s.get("c") is not None


def test_touch_extends_ttl():
    s = PendingStore(ttl=0.15, touch=True, reg=False)
    s["a"] = {}
    time.sleep(0.1)
    assert s.get("a") is not None
    time.sleep(0.1)
    assert s.get("a") is not None
    time.sleep(0.2)
    assert s.get("a") is None


def test_pop_and_setdefault():
    s = PendingStore(ttl=100, reg=False)
    s["a"] = {"n": 1}
    popped = s.pop("a")
    assert popped["n"] == 1
    assert "created" in popped
    assert s.pop("missing", "def") == "def"
    assert s.setdefault("b", {"y": 2})["y"] == 2


def test_pop_ignores_expired():
    s = PendingStore(ttl=0.05, reg=False)
    s["a"] = {"n": 1}
    time.sleep(0.1)
    assert s.pop("a", "def") == "def"
    assert "a" not in s


def test_setdefault_adds_created():
    s = PendingStore(ttl=100, reg=False)
    val = s.setdefault("a", {"n": 1})
    assert "created" in val
    assert s.setdefault("a", {"n": 2})["n"] == 1


def test_sweep_all():
    s = PendingStore(ttl=0.05, reg=True)
    s["x"] = {}
    time.sleep(0.1)
    sweep_all()
    assert len(s) == 0
