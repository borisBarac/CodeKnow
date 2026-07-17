"""Repository path normalization."""

from __future__ import annotations

from pathlib import Path


def repository_path(path: str | Path, root: Path) -> str:
    """Return a safe repository relative POSIX path."""
    root = root.resolve()
    candidate = Path(path)
    resolved = (
        candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    )
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        msg = f"Path escapes repository root: {path}"
        raise ValueError(msg) from exc
    return relative.as_posix()


def repository_file(path: str | Path, root: Path) -> Path:
    """Resolve a stored repository path for reading."""
    relative = repository_path(path, root)
    return root.resolve() / relative
