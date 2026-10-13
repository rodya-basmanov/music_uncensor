"""Сетевые утилиты: проверка доступности прокси."""

import socket
from typing import Optional, Tuple
from urllib.parse import urlparse


def parse_host_port(url: str) -> Optional[Tuple[str, int]]:
    """Достаёт (host, port) из URL прокси (socks5/http/https)."""
    if not url:
        return None
    try:
        parsed = urlparse(url if "://" in url else f"//{url}")
        host = parsed.hostname
        if not host:
            return None
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme in ("https", "socks5h") else 80
        return host, int(port)
    except (ValueError, TypeError):
        return None


def tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """True, если TCP-соединение к host:port устанавливается."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def proxy_reachable(url: str, timeout: float = 2.0) -> bool:
    """True, если прокси по URL принимает TCP-соединения."""
    hp = parse_host_port(url)
    if not hp:
        return False
    return tcp_reachable(hp[0], hp[1], timeout)
