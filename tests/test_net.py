"""Тесты сетевых утилит (utils/net.py)."""

import socket

from utils.net import parse_host_port, proxy_reachable, tcp_reachable


def test_parse_host_port_socks5():
    assert parse_host_port("socks5://127.0.0.1:10808") == ("127.0.0.1", 10808)


def test_parse_host_port_default_ports():
    assert parse_host_port("http://1.2.3.4") == ("1.2.3.4", 80)
    assert parse_host_port("https://proxy.example") == ("proxy.example", 443)


def test_parse_host_port_invalid():
    assert parse_host_port("") is None


def test_proxy_reachable_true(monkeypatch):
    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: FakeSock())
    assert proxy_reachable("socks5://127.0.0.1:10808") is True
    assert tcp_reachable("127.0.0.1", 10808) is True


def test_proxy_reachable_false(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(socket, "create_connection", boom)
    assert proxy_reachable("socks5://127.0.0.1:10808") is False
