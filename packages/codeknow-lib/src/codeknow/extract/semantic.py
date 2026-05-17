"""Semantic extraction via LangChain.

Stub — full implementation in Plan 4. This module will use
``langchain`` with a configurable model provider to extract conceptual
entities from docs/markdown/papers.

Returns ``ExtractionResult`` with ``confidence=INFERRED`` or ``AMBIGUOUS``.
"""

from __future__ import annotations

from typing import Any

from codeknow.schemas import ExtractionResult


def extract_semantic(
    files: dict[str, list[str]],
    *,
    model: str = "gpt-4o",
    **kwargs: Any,
) -> ExtractionResult:
    """Extract conceptual entities from non-code files using LangChain.

    Stub — returns empty results. Will be implemented in Plan 4.
    """
    return ExtractionResult()
