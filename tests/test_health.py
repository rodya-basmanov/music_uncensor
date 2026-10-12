"""Тесты heartbeat и алертов (services/health.py)."""

import asyncio

from services import health


def test_beat_and_uptime():
    health.mark_start()
    assert health.uptime() >= 0
    assert health.seconds_since_beat() < 1
    health.beat()
    assert health.seconds_since_beat() < 1


def test_alert_owner_without_owner():
    health.configure(lambda: None, None)
    assert asyncio.run(health.alert_owner("x", key="no_owner_key")) is False


def test_alert_owner_sends_then_cooldown():
    sent: list[tuple[int, str]] = []

    async def send_dm(uid: int, text: str) -> bool:
        sent.append((uid, text))
        return True

    health.configure(lambda: 555, send_dm)
    assert asyncio.run(health.alert_owner("hello", key="cooldown_key")) is True
    assert sent and sent[0][0] == 555
    # повтор в пределах cooldown не отправляется
    assert asyncio.run(health.alert_owner("again", key="cooldown_key")) is False
    assert len(sent) == 1
