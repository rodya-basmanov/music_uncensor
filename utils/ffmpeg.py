"""Утилиты ffmpeg: путь к бинарю, ресайз обложек."""

import os
import shutil
import subprocess
from typing import List

from utils.logger import get_logger

log = get_logger("ffmpeg")


def get_ffmpeg_path() -> str:
    """Возвращает путь к ffmpeg (системный или из imageio-ffmpeg)."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def get_ytdlp_ffmpeg_args() -> List[str]:
    """Возвращает --ffmpeg-location для yt-dlp, если нужно."""
    path = get_ffmpeg_path()
    if path != "ffmpeg":
        return ["--ffmpeg-location", path]
    return []


def make_thumbnail(src_image: str, dest_jpg: str, size: int = 320) -> bool:
    """Ресайзит обложку в квадрат size×size (jpg, ≤200КБ) для Telegram.

    Returns:
        True если файл успешно создан.
    """
    if not src_image or not os.path.exists(src_image):
        return False

    ffmpeg = get_ffmpeg_path()
    # Центрируем и обрезаем в квадрат (без чёрных полос)
    vf = (
        f"scale={size}:{size}:force_original_aspect_ratio=increase,"
        f"crop={size}:{size}"
    )
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", src_image,
        "-vf", vf,
        "-q:v", "5",
        dest_jpg,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and os.path.exists(dest_jpg):
            size_kb = os.path.getsize(dest_jpg) / 1024
            # Telegram требует ≤200КБ
            if size_kb > 200:
                retry = cmd[:-1] + ["-q:v", "12", dest_jpg]
                subprocess.run(retry, capture_output=True, text=True, timeout=30)
            return os.path.exists(dest_jpg)
        log.warning("thumbnail_resize_failed", stderr=result.stderr[:200])
        return False
    except Exception as e:
        log.warning("thumbnail_resize_error", error=str(e))
        return False
