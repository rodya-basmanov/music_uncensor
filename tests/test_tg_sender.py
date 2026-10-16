"""Тесты tg_sender: ретраи и пер-чат FloodWait (без сети)."""

import asyncio
import time

import pytest
from aiogram.exceptions import TelegramEntityTooLarge, TelegramServerError

from services import tg_sender


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _reset():
    tg_sender.set_send_interval(0)
    tg_sender._chat_locks.clear()
    tg_sender._chat_last.clear()
    tg_sender._chat_times.clear()
    tg_sender._chat_blocked_until.clear()
    yield


def test_retries_on_server_error():
    calls = {"n": 0}

    async def make_call():
        calls["n"] += 1
        if calls["n"] < 2:
            raise TelegramServerError(method="sendAudio", message="bad gateway")
        return "ok"

    sender = tg_sender.TelegramSender(bot=None)
    res = _run(sender._send_with_retry(make_call, chat_id=1, max_retries=3))
    assert res == "ok"
    assert calls["n"] == 2


def test_entity_too_large_not_retried():
    calls = {"n": 0}

    async def make_call():
        calls["n"] += 1
        raise TelegramEntityTooLarge(method="sendAudio", message="too big")

    sender = tg_sender.TelegramSender(bot=None)
    with pytest.raises(TelegramEntityTooLarge):
        _run(sender._send_with_retry(make_call, chat_id=1, max_retries=3))
    assert calls["n"] == 1


def test_flood_wait_is_per_chat():
    tg_sender._block_chat(111, 5)
    assert tg_sender._chat_blocked_until[111] > time.time()
    assert 222 not in tg_sender._chat_blocked_until
