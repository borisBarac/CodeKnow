from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from codeknow.schemas import ExtractionResult


@dataclass
class CacheStats:
    entry_count: int = 0
    total_bytes: int = 0
    ttl_seconds: int | None = None


@runtime_checkable
class CacheStore(Protocol):
    async def get(self, path: Path, root: Path) -> ExtractionResult | None: ...
    async def store(
        self, path: Path, result: ExtractionResult | dict[str, Any], root: Path
    ) -> None: ...
    async def has(self, path: Path, root: Path) -> bool: ...
    async def delete(self, path: Path, root: Path) -> None: ...
    async def stats(self) -> CacheStats: ...
    async def evict(self, active_files: set[Path], root: Path) -> int: ...
    async def close(self) -> None: ...
