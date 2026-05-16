from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from codeknow.schemas import ExtractionResult

from .hash import file_hash
from .protocol import CacheStats


class FileCacheStore:
    def __init__(self) -> None:
        pass

    def cache_dir(self, root: Path = Path()) -> Path:
        d = Path(root).resolve() / "graph-out" / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def get(self, path: Path, root: Path = Path()) -> ExtractionResult | None:
        data = self._load_raw(path, root)
        if data is None:
            return None
        try:
            return ExtractionResult.model_validate(data)
        except Exception:
            return None

    def get_raw(self, path: Path, root: Path = Path()) -> dict[str, Any] | None:
        return self._load_raw(path, root)

    def _load_raw(self, path: Path, root: Path = Path()) -> dict[str, Any] | None:
        try:
            h = file_hash(path, root)
        except OSError:
            return None
        entry = self.cache_dir(root) / f"{h}.json"
        if not entry.exists():
            return None
        try:
            return json.loads(entry.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except (json.JSONDecodeError, OSError):
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
        data = result.model_dump() if isinstance(result, ExtractionResult) else result
        h = file_hash(p, root)
        entry = self.cache_dir(root) / f"{h}.json"
        tmp = entry.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            try:
                tmp.replace(entry)
            except PermissionError:
                import shutil

                shutil.copy2(tmp, entry)
                tmp.unlink(missing_ok=True)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    async def has(self, path: Path, root: Path = Path()) -> bool:
        try:
            h = file_hash(path, root)
        except OSError:
            return False
        return (self.cache_dir(root) / f"{h}.json").exists()

    async def delete(self, path: Path, root: Path = Path()) -> None:
        try:
            h = file_hash(path, root)
        except OSError:
            return
        entry = self.cache_dir(root) / f"{h}.json"
        if entry.exists():
            entry.unlink()

    async def stats(self) -> CacheStats:
        return CacheStats()

    async def evict(self, active_files: set[Path], root: Path = Path()) -> int:
        return 0

    async def close(self) -> None:
        pass

    def cached_files(self, root: Path = Path()) -> set[str]:
        d = self.cache_dir(root)
        return {p.stem for p in d.glob("*.json")}

    def clear_cache(self, root: Path = Path()) -> None:
        d = self.cache_dir(root)
        for f in d.glob("*.json"):
            f.unlink()

    def check_semantic_cache(
        self,
        files: list[str],
        root: Path = Path(),
    ) -> tuple[
        list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]
    ]:
        cached_nodes: list[dict[str, Any]] = []
        cached_edges: list[dict[str, Any]] = []
        cached_hyperedges: list[dict[str, Any]] = []
        uncached: list[str] = []

        for fpath in files:
            result = self._load_raw(Path(fpath), root)
            if result is not None:
                cached_nodes.extend(result.get("nodes", []))
                cached_edges.extend(result.get("edges", []))
                cached_hyperedges.extend(result.get("hyperedges", []))
            else:
                uncached.append(fpath)

        return cached_nodes, cached_edges, cached_hyperedges, uncached

    def save_semantic_cache(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        hyperedges: list[dict[str, Any]] | None = None,
        root: Path = Path(),
    ) -> int:
        by_file: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"nodes": [], "edges": [], "hyperedges": []}
        )
        for n in nodes:
            src = n.get("source_file", "")
            if src:
                by_file[src]["nodes"].append(n)
        for e in edges:
            src = e.get("source_file", "")
            if src:
                by_file[src]["edges"].append(e)
        for he in hyperedges or []:
            src = he.get("source_file", "")
            if src:
                by_file[src]["hyperedges"].append(he)

        saved = 0
        for fpath, result in by_file.items():
            p = Path(fpath)
            if not p.is_absolute():
                p = Path(root) / p
            if p.is_file():
                self._store_sync(p, result, root)
                saved += 1
        return saved

    def _store_sync(
        self,
        path: Path,
        result: ExtractionResult | dict[str, Any],
        root: Path = Path(),
    ) -> None:
        p = Path(path)
        if not p.is_file():
            return
        data = result.model_dump() if isinstance(result, ExtractionResult) else result
        h = file_hash(p, root)
        entry = self.cache_dir(root) / f"{h}.json"
        tmp = entry.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            try:
                tmp.replace(entry)
            except PermissionError:
                import shutil

                shutil.copy2(tmp, entry)
                tmp.unlink(missing_ok=True)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
