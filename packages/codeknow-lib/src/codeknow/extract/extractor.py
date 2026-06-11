from __future__ import annotations

from pathlib import Path
from typing import Any

from codeknow.extract.ast import _check_tree_sitter_version, extract
from codeknow.extract.detect import detect


class Extractor:
    """Single testable seam for the extraction pipeline.

    Wraps file discovery (detect) + AST extraction (extract) into one
    callable interface. Downstream callers (pipeline runner, e2e tests)
    can use ``Extractor.extract(repo_path)`` instead of wiring detect +
    extract_ast together.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir
        _check_tree_sitter_version()

    def extract(self, repo_path: Path) -> dict[str, Any]:
        """Discover files in *repo_path* and extract AST nodes + edges.

        Returns the standard extraction dict with keys ``nodes``, ``edges``,
        ``input_tokens``, ``output_tokens``.
        """
        discovery = self._discover_files(repo_path)
        code_paths = [Path(p) for p in discovery["files"].get("code", [])]
        if not code_paths:
            return {
                "nodes": [],
                "edges": [],
                "input_tokens": 0,
                "output_tokens": 0,
            }
        return extract(code_paths, self._cache_dir)

    def discover(self, repo_path: Path) -> dict[str, Any]:
        """Return the file discovery dict without extracting."""
        return self._discover_files(repo_path)

    def _discover_files(self, repo_path: Path) -> dict[str, Any]:
        return detect(repo_path)
