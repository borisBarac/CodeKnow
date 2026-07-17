"""Tests for git_download — clone and update git repos."""

from pathlib import Path
from unittest.mock import patch

from codeknow.git_download import diff_changes, download, get_commit_hash, is_cloned
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


def test_download_preserves_github_ssh_url_for_clone(
    tmp_path: Path,
) -> None:
    target = tmp_path / "clone"
    with (
        patch("codeknow.git_download.downloader.Repo.clone_from") as mock_clone,
        patch("codeknow.git_download.downloader.fetch_and_checkout") as mock_fetch,
    ):
        download("git@github.com:nestjs/nest.git", target)

    mock_clone.assert_called_once_with("git@github.com:nestjs/nest.git", target)
    mock_fetch.assert_called_once_with(target, branch=None)


def test_download_preserves_github_ssh_url_for_existing_origin(
    tmp_path: Path,
) -> None:
    target = tmp_path / "clone"
    repo_url = "git@github.com:nestjs/nest.git"
    with (
        patch("codeknow.git_download.downloader.is_cloned", return_value=True),
        patch("codeknow.git_download.downloader.Repo") as mock_repo,
    ):
        origin = mock_repo.return_value.remotes.origin
        origin.url = "https://github.com/nestjs/nest.git"

        download(repo_url, target)

    origin.set_url.assert_called_once_with(repo_url)
    origin.fetch.assert_called_once_with(prune=True)


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


def test_get_commit_hash_returns_none_for_non_git_directory(tmp_path: Path) -> None:
    source_snapshot = tmp_path / "source-snapshot"
    source_snapshot.mkdir()

    assert get_commit_hash(source_snapshot) is None


def test_diff_changes_reads_real_nul_separated_git_output(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    original = root / "original.py"
    modified = root / "tab\tname.py"
    original.write_text("\n".join(f"line {i}" for i in range(20)), encoding="utf-8")
    modified.write_text("before\n", encoding="utf-8")
    repo = Repo.init(root)
    repo.index.add([original.name, modified.name])
    old_sha = repo.index.commit("initial").hexsha

    renamed = root / "renamed.py"
    original.rename(renamed)
    modified.write_text("after\n", encoding="utf-8")
    repo.index.remove([original.name])
    repo.index.add([renamed.name, modified.name])
    new_sha = repo.index.commit("update").hexsha

    changes = diff_changes(root, old_sha, new_sha)

    assert any(
        change.status == "R"
        and change.old_path == original.name
        and change.path == renamed.name
        for change in changes
    )
    assert any(
        change.status == "M" and change.path == modified.name for change in changes
    )
