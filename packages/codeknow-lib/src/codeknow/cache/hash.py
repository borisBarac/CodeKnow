from __future__ import annotations

import hashlib
from pathlib import Path

from codeknow.pipeline.config import EXTRACTION_CACHE_VERSION

_CHUNK_SIZE = 64 * 1024


def _body_content(content: bytes) -> bytes:
    text = content.decode(errors="replace")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].encode()
    return content


def file_hash(path: Path, root: Path = Path()) -> str:
    p = Path(path)
    if not p.is_file():
        msg = f"file_hash requires a file, got: {p}"
        raise IsADirectoryError(msg)

    h = hashlib.sha256()
    h.update(f"extraction:{EXTRACTION_CACHE_VERSION}".encode())
    h.update(b"\x00")

    if p.suffix.lower() == ".md":
        h.update(_body_content(p.read_bytes()))
    else:
        with p.open("rb") as f:
            while chunk := f.read(_CHUNK_SIZE):
                h.update(chunk)

    h.update(b"\x00")
    try:
        rel = p.resolve().relative_to(Path(root).resolve())
        h.update(str(rel).encode())
    except ValueError:
        h.update(str(p.resolve()).encode())
    return h.hexdigest()
