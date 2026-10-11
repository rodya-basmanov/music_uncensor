"""Тесты менеджера кэша (core/cache_manager.py)."""

import json
import os
import time

from core.cache_manager import CacheManager


def test_cache_save_get_file_id(tmp_path):
    cm = CacheManager(cache_dir=str(tmp_path), ttl_days=7, max_size_gb=1)
    hk = "a" * 64

    cm.save(hk, {"artist": "A", "title": "B", "file_path": "x"})
    data = cm.get(hk)
    assert data["artist"] == "A"
    assert "accessed_at" in data

    cm.save_file_id(hk, "FID")
    assert cm.get(hk)["telegram_file_id"] == "FID"

    cm.update_access(hk)
    assert cm.get(hk)["accessed_at"] > 0


def test_cache_paths_and_has_file(tmp_path):
    cm = CacheManager(cache_dir=str(tmp_path))
    hk = "b" * 64
    assert cm._file_path(hk).endswith(f"{hk}.mp3")
    assert cm._cover_path(hk).endswith(f"{hk}.jpg")
    assert cm.has_file(hk) is False

    with open(cm._file_path(hk), "wb") as f:
        f.write(b"x")
    assert cm.has_file(hk) is True


async def test_cache_cleanup_by_ttl(tmp_path):
    cm = CacheManager(cache_dir=str(tmp_path), ttl_days=7, max_size_gb=1)
    hk = "c" * 64

    file_path = cm._file_path(hk)
    with open(file_path, "wb") as f:
        f.write(b"x" * 10)

    meta_path = cm._meta_path(hk)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "artist": "A", "title": "B", "file_path": file_path,
            "accessed_at": int(time.time()) - 10 * 86400,
        }, f)

    deleted = await cm.cleanup()
    assert deleted >= 1
    assert not os.path.exists(meta_path)
    assert not os.path.exists(file_path)


async def test_cache_cleanup_keeps_fresh(tmp_path):
    cm = CacheManager(cache_dir=str(tmp_path), ttl_days=7, max_size_gb=1)
    hk = "d" * 64
    with open(cm._file_path(hk), "wb") as f:
        f.write(b"x" * 10)
    cm.save(hk, {"artist": "A", "title": "B", "file_path": cm._file_path(hk)})

    deleted = await cm.cleanup()
    assert deleted == 0
    assert cm.get(hk) is not None
