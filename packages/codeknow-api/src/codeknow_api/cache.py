"""Redis-based response cache for search endpoints."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)

_redis: Any = None
_redis_enabled: bool = bool(os.getenv("CODEKNOW_REDIS_URL"))

DEFAULT_TTL = int(os.getenv("CODEKNOW_CACHE_TTL", "300"))


async def get_redis() -> Any:
    """Return a lazily-initialised ``redis.asyncio.Redis`` singleton.

    Returns ``None`` when ``CODEKNOW_REDIS_URL`` is not set, which
    disables caching entirely without any connection attempts.
    """
    global _redis  # noqa: PLW0603
    if not _redis_enabled:
        return None
    if _redis is not None:
        return _redis
    import redis.asyncio as aioredis

    url = os.getenv("CODEKNOW_REDIS_URL", "")
    _redis = aioredis.from_url(url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    """Shut down the shared Redis connection (call on app shutdown)."""
    global _redis  # noqa: PLW0603
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def _make_key(query: str, repos: list[str] | None, top_k: int) -> str:
    repos_sorted = sorted(repos) if repos is not None else None
    raw = json.dumps({"q": query, "repos": repos_sorted, "k": top_k})
    h = hashlib.sha256(raw.encode()).hexdigest()
    return f"ck:search:{h}"


async def invalidate_for_slug(slug: str) -> None:
    """Best-effort removal of cached search results that reference *slug*.

    We scan keys matching ``ck:search:*`` and delete those whose stored
    JSON body contains the slug in a structured field (``repos`` list or
    top-level ``slug`` key).  This avoids false positives from naive
    substring matching on serialised JSON.
    """
    redis = await get_redis()
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


def _body_references_slug(data: Any, slug: str) -> bool:
    """Check whether a parsed cache payload references *slug*.

    Inspects known structured fields (``repos`` list, top-level
    ``slug`` key, and ``slug`` within result items) instead of doing
    a raw substring search on the serialised JSON.
    """
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
