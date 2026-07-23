from __future__ import annotations

import pytest
from codeknow.chunking.chunker import chunk_file_linear


def test_chunker_rejects_invalid_overlap(tmp_path) -> None:
    path = tmp_path / "main.py"
    path.write_text("print('hi')\n", encoding="utf-8")

    with pytest.raises(ValueError, match="overlap must satisfy"):
        chunk_file_linear(str(path), chunk_size=10, overlap=10)


def test_whitespace_chunk_is_marked_not_embeddable(tmp_path) -> None:
    path = tmp_path / "empty.py"
    path.write_text(" \n\t\n", encoding="utf-8")

    chunks = chunk_file_linear(str(path))

    assert len(chunks) == 1
    assert chunks[0].embeddable is False
