"""Тесты UI-хелперов (utils/ui.py)."""

from utils.ui import fmt_duration_mmss, is_mashup, progress_bar, source_icon, version_tag


def test_progress_bar():
    assert progress_bar(0, 10) == "⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 0%"
    assert progress_bar(10, 10) == "🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 100%"
    assert progress_bar(5, 10) == "🟩🟩🟩🟩🟩⬜⬜⬜⬜⬜ 50%"
    assert progress_bar(0, 0) == ""


def test_is_mashup():
    assert is_mashup("FLAGMAN (widee flip)") is True
    assert is_mashup("track remix 2024") is True
    assert is_mashup("SALUKI - Огней") is False
    assert is_mashup("credit card") is False
    assert is_mashup("smash hit") is False


def test_source_icon():
    assert source_icon("youtube") == "▶️"
    assert source_icon("soundcloud") == "☁️"
    assert source_icon("unknown") == "🎵"


def test_fmt_duration_mmss():
    assert fmt_duration_mmss(147) == "2:27"
    assert fmt_duration_mmss(0) == "?"
    assert fmt_duration_mmss(3661) == "1:01:01"
    assert fmt_duration_mmss(None) == "?"


def test_version_tag():
    assert version_tag("Song (Live)") == "🎬"
    assert version_tag("Song remix") == "🔀"
    assert version_tag("Song (Live) remix") == "🎬🔀"
    assert version_tag("Обычный трек") == ""
