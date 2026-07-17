from __future__ import annotations

import fcntl
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@contextmanager
def slug_build_lock(graph_dir: Path, slug: str) -> Iterator[None]:
    """Serialize builds and deletion for one repository slug."""
    lock_dir = graph_dir / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / f"{slug}.lock"
    with path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
