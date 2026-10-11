"""Тесты логики процессора (services/processor.py) — чистые функции."""

from core.scraper.common import Candidate, DownloadResult
from services.processor import _is_junk, _effective_order, _resolve_display


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
