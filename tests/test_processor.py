"""Тесты логики процессора (services/processor.py) — чистые функции."""

from core.scraper.common import Candidate, DownloadResult
from services import processor, tg_sender
from services.processor import (
    _effective_order,
    _is_junk,
    _resolve_display,
    get_retry,
    register_retry,
)
from utils.helpers import Track


def test_is_junk():
    assert _is_junk("5") is True
    assert _is_junk("") is True
    assert _is_junk("x") is True
    assert _is_junk("SALUKI") is False


def test_effective_order():
    order = ["youtube", "soundcloud"]
    assert _effective_order(order, False) == ["youtube", "soundcloud"]
    assert _effective_order(order, True) == ["soundcloud", "youtube"]


def test_resolve_display_junk_artist_uses_title_split():
    dr = DownloadResult(file_path="x", artist="5", title="SALUKI - Огней")
    cand = Candidate(url="u", title="SALUKI - Огней", uploader="5")
    assert _resolve_display(dr, cand, "") == ("SALUKI", "Огней")


def test_resolve_display_uploader_fallback():
    dr = DownloadResult(file_path="x", artist="", title="ОГНЕЙ")
    cand = Candidate(url="u", title="ОГНЕЙ", uploader="SALUKI")
    assert _resolve_display(dr, cand, "") == ("SALUKI", "ОГНЕЙ")


def test_resolve_display_uses_source_artist():
    dr = DownloadResult(file_path="x", artist="SALUKI", title="ОГНЕЙ")
    cand = Candidate(url="u", title="ОГНЕЙ", uploader="nesatyi")
    assert _resolve_display(dr, cand, "") == ("SALUKI", "ОГНЕЙ")


def test_resolve_display_mashup_uses_uploader():
    dr = DownloadResult(file_path="x", artist="", title="FL@GMAN (widee flip) macan flagman remix")
    cand = Candidate(url="u", title="FL@GMAN (widee flip) macan flagman remix", uploader="wexwie")
    artist, title = _resolve_display(dr, cand, "")
    assert artist == "wexwie"
    assert title == "FL@GMAN (widee flip) macan flagman remix"


def test_build_caption_links():
    processor.set_caption(True)
    tg_sender.set_bot_username("my_bot")
    cap = processor._build_caption("https://soundcloud.com/a/b")
    assert cap is not None
    assert '<a href="https://soundcloud.com/a/b">SoundCloud</a>' in cap
    assert '<a href="https://t.me/my_bot">via</a>' in cap


def test_build_caption_unknown_source():
    processor.set_caption(True)
    assert processor._build_caption("https://psv4.vkuseraudio.net/x") is None


def test_build_caption_disabled():
    processor.set_caption(False)
    assert processor._build_caption("https://soundcloud.com/a/b") is None
    processor.set_caption(True)


def test_register_retry_roundtrip():
    tracks = [Track(artist="A", title="B", duration=10)]
    token = register_retry(tracks, channel_id=-100, user_id=42, mode="vk")
    data = get_retry(token)
    assert data is not None
    assert data["tracks"] == tracks
    assert data["channel_id"] == -100
    assert data["user_id"] == 42
    assert data["mode"] == "vk"


def test_get_retry_unknown_token():
    assert get_retry("nope-not-a-token") is None
