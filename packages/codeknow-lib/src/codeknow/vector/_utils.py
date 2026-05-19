"""Shared utilities for the vector sub-package."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codeknow.schemas import Chunk


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
