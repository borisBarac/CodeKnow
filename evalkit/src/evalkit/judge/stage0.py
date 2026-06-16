"""Stage 0 — deterministic citation verification (no LLM, no cost).

Verifies every cited ``file:line`` exists in the repo, extracts the real code
snippet at each location, and computes citation-set consistency (Jaccard)
across seeds. Output feeds Stage 1 (grounding/faithfulness) and Stage 2
(pairwise).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from pathlib import Path


class Stage0Result(BaseModel):
    """Output of Stage 0 for a single run.

    - ``existence_rate`` — fraction of cited ``file:line`` whose file exists.
      ``None`` when there are no citations (vacuous — distinct from "0% of
      citations existed", which would imply citations were checked and found
      missing).
    - ``existence_map`` — per-citation existence verdict (fed to reports).
    - ``snippets`` — real code at each existing citation; ``None`` when the
      file is missing (so Stage 1 can flag it as ``[FILE NOT FOUND]``).
    """

    existence_rate: float | None
    existence_map: dict[str, bool]
    snippets: dict[str, str | None]


def stage0(citations: list[str], repo_root: Path, context: int = 5) -> Stage0Result:
    """Deterministic verification: existence + snippets for every citation."""
    existence_map = verify_existence(citations, repo_root)
    snippets: dict[str, str | None] = {}
    for citation, exists in existence_map.items():
        if not exists:
            snippets[citation] = None
            continue
        file_path, line = _split_citation(citation)
        snippets[citation] = extract_snippet(repo_root / file_path, line, context)
    rate = sum(existence_map.values()) / len(existence_map) if existence_map else None
    return Stage0Result(
        existence_rate=rate, existence_map=existence_map, snippets=snippets
    )


def citation_jaccard(citation_sets: list[set[str]]) -> float:
    """Jaccard similarity across two or more citation sets.

    Returns ``1.0`` when there is nothing to union (all empty) — the
    convention that "no citations" is treated as trivially consistent, so
    the consistency axis does not penalise a tool that found nothing.
    """
    if not citation_sets:
        return 1.0
    intersection = set.intersection(*citation_sets)
    union = set.union(*citation_sets)
    if not union:
        return 1.0
    return len(intersection) / len(union)


def verify_existence(citations: list[str], repo_root: Path) -> dict[str, bool]:
    """Return ``{citation: file_exists}`` for each cited ``file:line``.

    Existence is file-level (the file at ``citation``'s path exists under
    ``repo_root``); the line number is consumed by snippet extraction.
    """
    out: dict[str, bool] = {}
    for citation in citations:
        file_path, _line = _split_citation(citation)
        out[citation] = (repo_root / file_path).exists()
    return out


def _split_citation(citation: str) -> tuple[str, int]:
    """Split ``"src/a.py:144"`` into ``("src/a.py", 144)``.

    The path half is everything before the final ``:``; the line is the
    integer after it. A missing/unparseable line falls back to ``1``.
    """
    path, _, line_str = citation.rpartition(":")
    try:
        line = int(line_str)
    except ValueError:
        line = 1
    return path, line


def extract_snippet(path: Path, line: int, context: int = 5) -> str:
    """Return the ``±context`` lines around ``line`` (1-indexed) in ``path``.

    Bounds are clamped to ``[1, len(file)]``. Returns ``""`` if the file
    cannot be read (existence is checked separately by ``verify_existence``).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines:
        return ""
    start = max(line - context, 1)
    end = min(line + context, len(lines))
    return "\n".join(lines[start - 1 : end])
