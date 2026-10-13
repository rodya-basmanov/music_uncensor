"""Тесты детекта истёкшего VK-токена (core/vk_fetcher.py)."""

from core.vk_fetcher import _is_auth_error


def test_is_auth_error_expired():
    assert _is_auth_error("[5] User authorization failed: access_token has expired.") is True


def test_is_auth_error_invalid():
    assert _is_auth_error("access_token is invalid") is True


def test_is_auth_error_other():
    assert _is_auth_error("Too many requests per second") is False
