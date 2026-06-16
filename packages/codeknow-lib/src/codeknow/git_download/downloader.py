"""Clone or update a git repository to a local path.

Usage::

    from codeknow.git_download import download

    local_path = download(
        "https://github.com/OWNER/REPO.git",
        Path("/tmp/my-copy"),
    )
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

from git import Repo
from git.exc import InvalidGitRepositoryError


def is_cloned(target_path: Path) -> bool:
    """Return ``True`` when *target_path* already contains a git repo."""
    return (target_path / ".git").exists()


def get_commit_hash(target_path: Path) -> str | None:
    try:
        return Repo(target_path).head.commit.hexsha
    except (InvalidGitRepositoryError, ValueError):
        return None


def download(repo_url: str, target_path: Path) -> Path:
    """Clone the repo if missing, or pull latest if already present.

    Returns the local path so it plugs directly into
    ``PipelineConfig.root``.
    """
    if is_cloned(target_path):
        repo = Repo(target_path)
        repo.remotes.origin.pull()
    else:
        Repo.clone_from(repo_url, target_path)
    return target_path
