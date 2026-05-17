"""Tests for git_download — clone and update git repos."""

from pathlib import Path

from codeknow.git_download import download, is_cloned
from git import Repo


def _make_local_remote(tmp_path: Path) -> Path:
    """Create a minimal local git repo to use as a clone remote (no network)."""
    src = tmp_path / "remote"
    src.mkdir()
    (src / "README.md").write_text("# hello", encoding="utf-8")
    repo = Repo.init(src)
    repo.index.add(["README.md"])
    repo.index.commit("init")
    return src


def test_download_clones(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path)
    target = tmp_path / "clone"
    result = download(str(remote), target)
    assert result == target
    assert (target / ".git").exists()
    assert (target / "README.md").read_text() == "# hello"


def test_is_cloned_false(tmp_path: Path) -> None:
    assert is_cloned(tmp_path / "missing") is False


def test_is_cloned_true(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path)
    target = tmp_path / "clone"
    download(str(remote), target)
    assert is_cloned(target) is True


def test_download_pulls_existing(tmp_path: Path) -> None:
    remote = _make_local_remote(tmp_path)
    target = tmp_path / "clone"
    download(str(remote), target)
    (remote / "new.txt").write_text("added", encoding="utf-8")
    repo = Repo(remote)
    repo.index.add(["new.txt"])
    repo.index.commit("add file")
    download(str(remote), target)
    assert (target / "new.txt").exists()
