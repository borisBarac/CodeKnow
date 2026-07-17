from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from codeknow.paths import repository_file, repository_path
from codeknow.schemas import Chunk

if TYPE_CHECKING:
    from pathlib import Path


def test_repository_path_normalizes_to_relative_posix_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = root / "src" / "service.py"
    source.parent.mkdir(parents=True)
    source.touch()

    assert repository_path(source, root) == "src/service.py"
    assert repository_path("src/../src/service.py", root) == "src/service.py"
    assert repository_file("src/service.py", root) == source


@pytest.mark.parametrize("path", ["../secret.py", "../../outside/secret.py"])
def test_repository_path_rejects_parent_escape(tmp_path: Path, path: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    with pytest.raises(ValueError, match="escapes repository root"):
        repository_path(path, root)


def test_repository_path_rejects_absolute_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    with pytest.raises(ValueError, match="escapes repository root"):
        repository_path(tmp_path / "outside.py", root)


def test_repository_path_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes repository root"):
        repository_path("linked/secret.py", root)


def test_identical_content_in_different_files_has_different_vector_ids() -> None:
    first = Chunk(file="first.py", start_line=1, end_line=3, hash="a" * 64)
    second = Chunk(file="second.py", start_line=1, end_line=3, hash="a" * 64)

    assert first.hash == second.hash
    assert first.vector_id != second.vector_id


def test_vector_id_is_stable_for_same_file_range_and_content() -> None:
    values = {"file": "src/main.py", "start_line": 4, "end_line": 9, "hash": "b" * 64}

    assert Chunk(**values).vector_id == Chunk(**values).vector_id


def test_vector_id_is_serialized_in_chunk_map_records() -> None:
    chunk = Chunk(file="src/main.py", start_line=1, end_line=2, hash="c" * 64)

    assert chunk.model_dump()["vector_id"] == chunk.vector_id


def test_extraction_cache_version_changes_file_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codeknow.cache.hash as cache_hash

    source = tmp_path / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    original = cache_hash.file_hash(source, tmp_path)

    monkeypatch.setattr(cache_hash, "EXTRACTION_CACHE_VERSION", 999)

    assert cache_hash.file_hash(source, tmp_path) != original
