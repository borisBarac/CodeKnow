from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis.asyncio as _aioredis

    from .protocol import CacheStore


def get_cache_store(
    backend: str | None = None,
    *,
    redis_client: _aioredis.Redis | None = None,
    graph_id: str = "default",
    ttl: int | None = None,
    max_result_bytes: int = 10 * 1024 * 1024,
    **kwargs: Any,
) -> CacheStore:
    name = backend or os.environ.get("CODEKNOW_CACHE_BACKEND", "file")

    if name == "file":
        from .file import FileCacheStore

        return FileCacheStore(**kwargs)

    if name == "redis":
        if redis_client is None:
            msg = "redis_client is required for Redis backend"
            raise ValueError(msg)
        from .redis import AsyncRedisCacheStore

        return AsyncRedisCacheStore(
            client=redis_client,
            graph_id=graph_id,
            ttl=ttl,
            max_result_bytes=max_result_bytes,
        )

    msg = f"Unknown cache backend: {name}"
    raise ValueError(msg)
