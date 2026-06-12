"""Redis-based response cache for search endpoints."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TTL = int(os.getenv("CODEKNOW_CACHE_TTL", "300"))

_default_service: RedisService | None = None


def _get_default_service() -> RedisService:
    global _default_service  # noqa: PLW0603
    if _default_service is None:
        _default_service = RedisService.from_env()
    return _default_service


def set_default_service(service: RedisService) -> None:
    """Replace the module-level default service (for testing)."""
    global _default_service  # noqa: PLW0603
    _default_service = service


async def get_redis() -> Any:
    """Return a lazily-initialised ``redis.asyncio.Redis`` client.

    Returns ``None`` when ``CODEKNOW_REDIS_URL`` is not set.
    """
    return await _get_default_service().get_client()


async def close_redis() -> None:
    """Shut down the default Redis connection."""
    global _default_service  # noqa: PLW0603
    if _default_service is not None:
        await _default_service.close()
        _default_service = None


class RedisService:
    """Encapsulates Redis connection lifecycle and cache operations."""

    def __init__(self, enabled: bool = False, url: str = "") -> None:
        self._enabled = enabled
        self._url = url
        self._client: Any = None

    @classmethod
    def from_env(cls) -> RedisService:
        url = os.getenv("CODEKNOW_REDIS_URL", "")
        return cls(enabled=bool(url), url=url)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def get_client(self) -> Any:
        if not self._enabled:
            return None
        if self._client is not None:
            return self._client
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(self._url, decode_responses=True)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def invalidate_for_slug(self, slug: str) -> None:
        redis = await self.get_client()
        if redis is None:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = await redis.scan(cursor, match="ck:search:*", count=100)
                if keys:
                    for key in keys:
                        val = await redis.get(key)
                        if val is None:
                            continue
                        try:
                            data = json.loads(val)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if _body_references_slug(data, slug):
                            await redis.delete(key)
                if cursor == 0:
                    break
        except Exception:
            logger.warning("Search cache invalidation failed", exc_info=True)


def _make_key(query: str, repos: list[str] | None, top_k: int) -> str:
    repos_sorted = sorted(repos) if repos is not None else None
    raw = json.dumps({"q": query, "repos": repos_sorted, "k": top_k})
    h = hashlib.sha256(raw.encode()).hexdigest()
    return f"ck:search:{h}"


def _body_references_slug(data: Any, slug: str) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("slug") == slug:
        return True
    repos = data.get("repos")
    if isinstance(repos, list) and slug in repos:
        return True
    results = data.get("results")
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict) and item.get("slug") == slug:
                return True
    return False


def cache_search(ttl: int | None = None) -> Any:
    """Decorator that caches the return value of a FastAPI search handler.

    The decorated function must accept a body that is either a
    ``dict[str, Any]`` or a Pydantic model with ``query``, ``top_k``, and
    ``repos`` attributes.  The cache key is derived from those three
    parameters so identical queries are served from Redis without
    re-executing the search.
    """
    _ttl = ttl or DEFAULT_TTL

    def decorator(func: Any) -> Any:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            raw = kwargs.get("body", args[0] if args else {})

            if isinstance(raw, dict):
                query = raw.get("query", "")
                top_k = raw.get("top_k", 10)
                repos = raw.get("repos")
            elif (
                hasattr(raw, "query")
                and hasattr(raw, "top_k")
                and hasattr(raw, "repos")
            ):
                query = raw.query
                top_k = raw.top_k
                repos = raw.repos
            else:
                query = ""
                top_k = 10
                repos = None

            cache_key = _make_key(query, repos, top_k)
            redis = await get_redis()

            if redis is not None:
                try:
                    cached = await redis.get(cache_key)
                    if cached is not None:
                        return json.loads(cached)
                except Exception:
                    logger.warning("Search cache read failed", exc_info=True)

            result = await func(*args, **kwargs)

            payload = result.model_dump() if hasattr(result, "model_dump") else result

            if redis is not None:
                try:
                    await redis.set(
                        cache_key,
                        json.dumps(payload, default=str),
                        ex=_ttl,
                    )
                except Exception:
                    logger.warning("Search cache write failed", exc_info=True)

            return payload

        return wrapper

    return decorator
