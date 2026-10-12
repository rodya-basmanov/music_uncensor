"""Тесты экранирования (utils/escape.py)."""

from utils.escape import esc_html, esc_md


def test_esc_html():
    assert esc_html("a < b & c") == "a &lt; b &amp; c"
    assert esc_html(None) == ""
    assert esc_html(123) == "123"


def test_esc_md():
    assert esc_md("a * b _ c [d]") == r"a \* b \_ c \[d\]"
    assert esc_md("normal text") == "normal text"
    assert esc_md(None) == ""
