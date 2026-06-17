"""Stage 0 — deterministic citation verification (no LLM, no cost).

Verifies every cited ``file:line`` exists in the repo and extracts the real
code snippet at each location. Output feeds Stage 1 (grounding/faithfulness)
and Stage 2 (pairwise).
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
        resolved = resolve_citation_path(file_path, repo_root)
        snippets[citation] = extract_snippet(resolved, line, context)
    rate = sum(existence_map.values()) / len(existence_map) if existence_map else None
    return Stage0Result(
        existence_rate=rate, existence_map=existence_map, snippets=snippets
    )


def verify_existence(citations: list[str], repo_root: Path) -> dict[str, bool]:
    """Return ``{citation: file_exists}`` for each cited ``file:line``.

    Existence is file-level (the file at ``citation``'s path exists under
    ``repo_root``); the line number is consumed by snippet extraction. Handles
    the three path forms the agents emit (see :func:`resolve_citation_path`).
    """
    return {
        citation: resolve_citation_path(_split_citation(citation)[0], repo_root)
        is not None
        for citation in citations
    }


def resolve_citation_path(file_path: str, repo_root: Path) -> Path | None:
    """Resolve a citation's file half to an existing path, or ``None``.

    Handles the three path forms the two tools emit:

    - **repo-relative** (grep + hybrid display layer): ``lib/route.js`` ->
      ``repo_root / "lib/route.js"``.
    - **absolute in-repo** (hybrid tool output): ``/abs/repo/lib/route.js`` ->
      that path. Pathlib's ``/`` discards ``repo_root`` when the right operand
      is absolute, so a single join covers this.
    - **root-relative** (grep tool output): ``/lib/route.js``. The natural join
      lands at the host's ``/lib/route.js`` (which does not exist on the eval
      host); fall back to ``repo_root / "lib/route.js"``. Without this fallback
      grep citations are systematically marked nonexistent even when the agent
      cited a real repo file, unfairly penalising grep in the eval.

    Returns ``None`` when no candidate resolves to an existing file.
    """
    direct = repo_root / file_path
    if direct.exists():
        return direct
    if file_path.startswith("/"):
        stripped = repo_root / file_path.lstrip("/")
        if stripped.exists():
            return stripped
    return None


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
