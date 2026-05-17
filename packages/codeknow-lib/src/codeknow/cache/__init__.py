"""Cache backends — file (default) and Redis."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .factory import get_cache_store
from .file import FileCacheStore
from .hash import file_hash
from .protocol import CacheStats, CacheStore

_default = FileCacheStore()


def __getattr__(name: str) -> object:
    if name == "AsyncRedisCacheStore":
        from . import redis as _redis_mod

        return getattr(_redis_mod, name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def load_cached(path: Path, root: Path = Path()) -> dict[str, Any] | None:
    result = asyncio.run(_default.get(path, root))
    return result.model_dump() if result is not None else None


def save_cached(path: Path, result: dict[str, Any], root: Path = Path()) -> None:
    asyncio.run(_default.store(path, result, root))


def cache_dir(root: Path = Path()) -> Path:
    return _default.cache_dir(root)


def cached_files(root: Path = Path()) -> set[str]:
    return _default.cached_files(root)


def clear_cache(root: Path = Path()) -> None:
    _default.clear_cache(root)


def check_semantic_cache(
    files: list[str],
    root: Path = Path(),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    return _default.check_semantic_cache(files, root)


def save_semantic_cache(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    hyperedges: list[dict[str, Any]] | None = None,
    root: Path = Path(),
) -> int:
    return _default.save_semantic_cache(nodes, edges, hyperedges, root)


__all__ = [
    "AsyncRedisCacheStore",
    "CacheStats",
    "CacheStore",
    "FileCacheStore",
    "cache_dir",
    "cached_files",
    "check_semantic_cache",
    "clear_cache",
    "file_hash",
    "get_cache_store",
    "load_cached",
    "save_cached",
    "save_semantic_cache",
]
