"""Тесты логгера: маскировка кредов (utils/logger.py)."""

from utils.logger import redact_secrets


def test_redact_secrets_in_values():
    ev = redact_secrets(None, "info", {
        "event": "proxy socks5://user:secret@127.0.0.1:1080 enabled",
        "url": "http://a:b@host",
        "safe": "no secrets here",
        "n": 5,
    })
    assert "***@" in ev["event"]
    assert "secret" not in ev["event"]
    assert ev["url"] == "http://***@host"
    assert ev["safe"] == "no secrets here"
    assert ev["n"] == 5
