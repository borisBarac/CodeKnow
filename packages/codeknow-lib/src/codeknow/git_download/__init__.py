"""Git repository downloader — clone or update repos for pipeline input."""

from .downloader import (
    GitChange,
    commit_exists,
    diff_changes,
    download,
    fetch_and_checkout,
    get_commit_hash,
    get_remote_branch,
    is_cloned,
    parse_diff_z,
)
from .repo_map import (
    DEFAULT_STORE_PATH,
    get_path,
    get_url,
    list_all,
    load,
    register,
    save,
    unregister,
)

__all__ = [
    "DEFAULT_STORE_PATH",
    "GitChange",
    "commit_exists",
    "diff_changes",
    "download",
    "fetch_and_checkout",
    "get_commit_hash",
    "get_path",
    "get_remote_branch",
    "get_url",
    "is_cloned",
    "list_all",
    "load",
    "parse_diff_z",
    "register",
    "save",
    "unregister",
]
