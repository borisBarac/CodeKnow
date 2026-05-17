from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codeknow.schemas import ExtractionResult

from .hash import file_hash
from .protocol import CacheStats

if TYPE_CHECKING:
    import redis.asyncio as _aioredis

logger = logging.getLogger(__name__)


def _decode(val: bytes | str | None) -> str | None:
    if val is None:
        return None
    return val if isinstance(val, str) else val.decode()


# Not used at the moment, fileStore is more optimal at the moment


class AsyncRedisCacheStore:
    def __init__(
        self,
        client: _aioredis.Redis,
        *,
        graph_id: str = "default",
        ttl: int | None = None,
        max_result_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._r = client
        self._graph_id = graph_id
        self._ttl = ttl
        self._max_bytes = max_result_bytes

    def _data_key(self, h: str) -> str:
        return f"ck:cache:{self._graph_id}:{h}"

    @property
    def _index_key(self) -> str:
        return f"ck:index:{self._graph_id}"

    def _bytes_key(self) -> str:
        return f"ck:stats:{self._graph_id}:bytes"

    def _rel_path(self, path: Path, root: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(Path(root).resolve()))
        except ValueError:
            return str(Path(path).resolve())

    async def get(self, path: Path, root: Path = Path()) -> ExtractionResult | None:
        rel = self._rel_path(path, root)
        raw_h = await self._r.hget(self._index_key, rel)
        h = _decode(raw_h)
        if h is None:
            return None
        data_raw: bytes | None = await self._r.get(self._data_key(h))
        if data_raw is None:
            await self._r.hdel(self._index_key, rel)
            return None
        try:
            data = json.loads(data_raw)
            return ExtractionResult.model_validate(data)
        except Exception:
            return None

    async def store(
        self,
        path: Path,
        result: ExtractionResult | dict[str, Any],
        root: Path = Path(),
    ) -> None:
        p = Path(path)
        if not p.is_file():
            return
        h = file_hash(p, root)
        payload = (
            result.model_dump() if isinstance(result, ExtractionResult) else result
        )
        raw = json.dumps(payload, default=str).encode()
        if len(raw) > self._max_bytes:
            logger.warning(
                "Cache entry for %s exceeds max_result_bytes (%d > %d), skipping",
                path,
                len(raw),
                self._max_bytes,
            )
            return
        rel = self._rel_path(p, root)
        async with self._r.pipeline() as pipe:
            await pipe.set(self._data_key(h), raw, ex=self._ttl)
            await pipe.hset(self._index_key, rel, h)
            await pipe.incrby(self._bytes_key(), len(raw))
            await pipe.execute()

    async def has(self, path: Path, root: Path = Path()) -> bool:
        rel = self._rel_path(path, root)
        return bool(await self._r.hexists(self._index_key, rel))

    async def delete(self, path: Path, root: Path = Path()) -> None:
        rel = self._rel_path(path, root)
        raw_h = await self._r.hget(self._index_key, rel)
        h = _decode(raw_h)
        if h is None:
            return
        data_raw: bytes | None = await self._r.get(self._data_key(h))
        async with self._r.pipeline() as pipe:
            await pipe.delete(self._data_key(h))
            await pipe.hdel(self._index_key, rel)
            if data_raw is not None:
                await pipe.decrby(self._bytes_key(), len(data_raw))
            await pipe.execute()

    async def stats(self) -> CacheStats:
        count = await self._r.hlen(self._index_key)
        total_bytes = int(await self._r.get(self._bytes_key()) or 0)
        ttl_seconds: int | None = None
        if self._ttl:
            _, sample = await self._r.hscan(self._index_key, 0, count=1)
            if sample:
                sh = _decode(next(iter(sample.values())))
                if sh:
                    raw_ttl = await self._r.ttl(self._data_key(sh))
                    if raw_ttl is not None and raw_ttl < 0:
                        raw_ttl = None
                    ttl_seconds = raw_ttl
        return CacheStats(
            entry_count=count,
            total_bytes=total_bytes,
            ttl_seconds=ttl_seconds,
        )

    async def evict(self, active_files: set[Path], root: Path = Path()) -> int:
        active_rels = {self._rel_path(p, root) for p in active_files}
        removed = 0
        cursor = 0
        while True:
            cursor, pairs = await self._r.hscan(self._index_key, cursor, count=100)
            async with self._r.pipeline() as pipe:
                for rel, h_val in pairs:
                    rel_str = rel if isinstance(rel, str) else rel.decode()
                    if rel_str not in active_rels:
                        h_s = h_val if isinstance(h_val, str) else h_val.decode()
                        data_raw = await self._r.get(self._data_key(h_s))
                        await pipe.delete(self._data_key(h_s))
                        await pipe.hdel(self._index_key, rel)
                        if data_raw is not None:
                            await pipe.decrby(self._bytes_key(), len(data_raw))
                        removed += 1
                if pipe.command_stack:
                    await pipe.execute()
            if cursor == 0:
                break
        return removed

    async def close(self) -> None:
        pass
