"""Tests for repo_map — persistent URL↔path mapping."""

# ruff: noqa: S108

from pathlib import Path

from codeknow.git_download.repo_map import (
    DEFAULT_STORE_PATH,
    get_path,
    get_url,
    list_all,
    load,
    register,
    save,
)


def test_register_and_get_path(tmp_path: Path) -> None:
    store = tmp_path / "repo_map.json"
    register("https://github.com/OWNER/REPO.git", Path("/tmp/repo"), store_path=store)
    result = get_path("https://github.com/OWNER/REPO.git", store_path=store)
    assert result == Path("/tmp/repo").resolve()


def test_get_path_missing(tmp_path: Path) -> None:
    store = tmp_path / "repo_map.json"
    assert get_path("https://github.com/UNKNOWN.git", store_path=store) is None


def test_get_url(tmp_path: Path) -> None:
    store = tmp_path / "repo_map.json"
    local = tmp_path / "my-repo"
    local.mkdir()
    register("https://github.com/OWNER/REPO.git", local, store_path=store)
    assert get_url(local, store_path=store) == "https://github.com/OWNER/REPO.git"


def test_get_url_missing(tmp_path: Path) -> None:
    store = tmp_path / "repo_map.json"
    assert get_url(Path("/no/such/path"), store_path=store) is None


def test_register_overwrites(tmp_path: Path) -> None:
    store = tmp_path / "repo_map.json"
    register("https://github.com/OWNER/REPO.git", Path("/old"), store_path=store)
    register("https://github.com/OWNER/REPO.git", Path("/new"), store_path=store)
    result = get_path("https://github.com/OWNER/REPO.git", store_path=store)
    assert result == Path("/new")


def test_list_all(tmp_path: Path) -> None:
    store = tmp_path / "repo_map.json"
    register("https://github.com/A/X.git", Path("/a"), store_path=store)
    register("https://github.com/B/Y.git", Path("/b"), store_path=store)
    m = list_all(store_path=store)
    assert len(m) == 2
    assert "https://github.com/A/X.git" in m
    assert "https://github.com/B/Y.git" in m


def test_load_missing_file(tmp_path: Path) -> None:
    assert load(store_path=tmp_path / "nonexistent.json") == {}


def test_round_trip(tmp_path: Path) -> None:
    store = tmp_path / "repo_map.json"
    mapping = {"https://github.com/O/R.git": "/some/path"}
    save(mapping, store_path=store)
    loaded = load(store_path=store)
    assert loaded == mapping


def test_get_url_resolves_relative(tmp_path: Path) -> None:
    store = tmp_path / "repo_map.json"
    local = tmp_path / "my-repo"
    local.mkdir()
    register("https://github.com/OWNER/REPO.git", local, store_path=store)
    assert get_url(local / ".." / "my-repo", store_path=store) is not None


def test_default_store_path() -> None:
    expected = Path.home() / ".codeknow" / "repo_map.json"
    assert expected == DEFAULT_STORE_PATH
