"""Shared utilities for the langchain sub-package."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codeknow.schemas import Chunk, HybridSearchResult


def read_chunk_content(chunk: Chunk) -> str:
    p = Path(chunk.file)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except OSError:
        return ""

    start = max(chunk.start_line - 1, 0)
    end = min(chunk.end_line, len(lines))
    return "".join(lines[start:end])


def sort_key(r: HybridSearchResult) -> tuple:
    provenance_order = {"vector": 0, "graph": 1}
    return (
        provenance_order.get(r.provenance, 2),
        r.distance if r.distance is not None else float("inf"),
        -(r.cumulative_weight or 0.0),
        len(r.graph_path or []),
    )
