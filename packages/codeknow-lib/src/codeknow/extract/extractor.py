from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar

from codeknow.cache import load_cached, save_cached
from codeknow.extract.ast import (
    _check_tree_sitter_version,
    _extract_js,
    _extract_python,
    _make_id,
    _resolve_cross_file_imports,
)
from codeknow.extract.detect import detect
from codeknow.paths import repository_path


def _reanchor_paths(cached: dict, current_path: str) -> None:
    """Rewrite stale ``source_file`` fields in a cached extraction result.

    Called on cache hits so that nodes, edges, and raw_calls point at the
    current file path rather than the path baked in at original extraction
    time.
    """
    for n in cached.get("nodes", []):
        if n.get("source_file"):
            n["source_file"] = current_path
    for e in cached.get("edges", []):
        if e.get("source_file"):
            e["source_file"] = current_path
    for rc in cached.get("raw_calls", []):
        if rc.get("source_file"):
            rc["source_file"] = current_path


def _stale_file_nid(cached: dict) -> str | None:
    """Return the file node ID derived from a cached result's stale source_file.

    The file node is the only node whose ID is location-dependent
    (``_make_id(str(path))``).  Before re-anchoring, we capture the stale
    file_nid so it can be remapped to the current path's file_nid.
    """
    for n in cached.get("nodes", []):
        src = n.get("source_file", "")
        if src:
            return _make_id(src)
    return None


class Extractor:
    """Single testable seam for the extraction pipeline.

    Wraps file discovery (detect) + AST extraction into one callable
    interface.  Downstream callers (pipeline runner, e2e tests) can use
    ``Extractor.extract(repo_path)`` instead of wiring detect + extract_ast
    together.
    """

    _DISPATCH: ClassVar[dict[str, Any]] = {
        ".py": _extract_python,
        ".js": _extract_js,
        ".jsx": _extract_js,
        ".mjs": _extract_js,
        ".ejs": _extract_js,
        ".ts": _extract_js,
        ".tsx": _extract_js,
    }

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        use_cache: bool = True,
    ) -> None:
        self._cache_dir = cache_dir
        self._use_cache = use_cache
        _check_tree_sitter_version()

    def extract(self, repo_path: Path) -> dict[str, Any]:
        """Discover files in *repo_path* and extract AST nodes + edges.

        Returns the standard extraction dict with keys ``nodes``, ``edges``,
        ``input_tokens``, ``output_tokens``.
        """
        discovery = self._discover_files(repo_path)
        return self.extract_from_discovery(discovery, repo_root=repo_path)

    def extract_from_discovery(
        self,
        discovery: dict[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> dict[str, Any]:
        """Extract AST from pre-discovered file lists.

        Args:
            discovery: dict with ``files.code`` list of paths (from
                :meth:`discover` or :func:`detect`).

        Returns:
            Extraction dict with ``nodes``, ``edges``, ``input_tokens``,
            ``output_tokens``.

        """
        code_paths = [Path(p) for p in discovery.get("files", {}).get("code", [])]
        if not code_paths:
            return {
                "nodes": [],
                "edges": [],
                "input_tokens": 0,
                "output_tokens": 0,
            }
        root = repo_root or self._common_root(code_paths)
        return self._extract(code_paths, root)

    def discover(self, repo_path: Path) -> dict[str, Any]:
        """Return the file discovery dict without extracting."""
        return self._discover_files(repo_path)

    def _discover_files(self, repo_path: Path) -> dict[str, Any]:
        return detect(repo_path)

    @staticmethod
    def _common_root(paths: list[Path]) -> Path:
        """Infer a root for compatibility with old direct callers."""
        if not paths:
            return Path.cwd()
        if len(paths) == 1:
            return paths[0].resolve().parent
        import os

        return Path(os.path.commonpath([str(path.resolve()) for path in paths]))

    def _extract(self, paths: list[Path], root: Path) -> dict[str, Any]:
        """Multi-file extraction orchestrator.

        Per-file dispatch → cache → merge → ID remap → cross-file resolution.
        """
        per_file: list[tuple[Path, dict]] = []
        stale_id_remap: dict[str, str] = {}

        root = root.resolve()

        for path in paths:
            extractor = self._DISPATCH.get(path.suffix)
            if extractor is None:
                continue
            cached = (
                load_cached(path, self._cache_dir or root) if self._use_cache else None
            )
            if cached is not None:
                stale_nid = _stale_file_nid(cached)
                current_nid = _make_id(str(path))
                _reanchor_paths(cached, str(path))
                if stale_nid is not None and stale_nid != current_nid:
                    stale_id_remap[stale_nid] = current_nid
                per_file.append((path, cached))
                continue
            result = extractor(path)
            if "error" not in result:
                save_cached(path, result, self._cache_dir or root)
            per_file.append((path, result))

        all_nodes: list[dict] = []
        all_edges: list[dict] = []
        for _, result in per_file:
            all_nodes.extend(result.get("nodes", []))
            all_edges.extend(result.get("edges", []))

        if stale_id_remap:
            for n in all_nodes:
                if n.get("id") in stale_id_remap:
                    n["id"] = stale_id_remap[n["id"]]
            for e in all_edges:
                if e.get("source") in stale_id_remap:
                    e["source"] = stale_id_remap[e["source"]]
                if e.get("target") in stale_id_remap:
                    e["target"] = stale_id_remap[e["target"]]

        id_remap: dict[str, str] = {}
        for path in paths:
            old_id = _make_id(str(path))
            try:
                new_id = _make_id(str(path.relative_to(root)))
            except ValueError:
                continue
            if old_id != new_id:
                id_remap[old_id] = new_id
        if id_remap:
            for n in all_nodes:
                if n.get("id") in id_remap:
                    n["id"] = id_remap[n["id"]]
            for e in all_edges:
                if e.get("source") in id_remap:
                    e["source"] = id_remap[e["source"]]
                if e.get("target") in id_remap:
                    e["target"] = id_remap[e["target"]]

        py_paths = [p for p in paths if p.suffix == ".py"]
        if py_paths:
            py_results = [result for path, result in per_file if path.suffix == ".py"]
            try:
                cross_file_edges = _resolve_cross_file_imports(py_results, py_paths)
                all_edges.extend(cross_file_edges)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Cross-file import resolution failed, skipping: %s", exc
                )

        global_label_to_nid: dict[str, str] = {}
        for n in all_nodes:
            raw = n.get("label", "")
            normalised = raw.strip("()").lstrip(".")
            if normalised:
                global_label_to_nid[normalised.lower()] = n["id"]

        existing_pairs = {(e["source"], e["target"]) for e in all_edges}
        for _, result in per_file:
            for rc in result.get("raw_calls", []):
                callee = rc.get("callee", "")
                if not callee:
                    continue
                tgt = global_label_to_nid.get(callee.lower())
                caller = rc.get("caller_nid")
                if caller is None:
                    continue
                if tgt and tgt != caller and (caller, tgt) not in existing_pairs:
                    existing_pairs.add((caller, tgt))
                    all_edges.append(
                        {
                            "source": caller,
                            "target": tgt,
                            "relation": "calls",
                            "confidence": "INFERRED",
                            "confidence_score": 0.8,
                            "source_file": rc.get("source_file", ""),
                            "source_location": rc.get("source_location"),
                            "weight": 1.0,
                        }
                    )

        for item in [*all_nodes, *all_edges]:
            source_file = item.get("source_file")
            if source_file:
                item["source_file"] = repository_path(source_file, root)

        return {
            "nodes": all_nodes,
            "edges": all_edges,
            "input_tokens": 0,
            "output_tokens": 0,
        }
