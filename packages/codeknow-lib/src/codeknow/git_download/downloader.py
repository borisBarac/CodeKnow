"""Clone or update a git repository to a local path.

Usage::

    from codeknow.git_download import download

    local_path = download(
        "https://github.com/OWNER/REPO.git",
        Path("/tmp/my-copy"),
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path  # noqa: TC003

from git import Repo
from git.exc import BadName, InvalidGitRepositoryError


@dataclass(frozen=True)
class GitChange:
    status: str
    path: str
    old_path: str | None = None


def is_cloned(target_path: Path) -> bool:
    """Return ``True`` when *target_path* already contains a git repo."""
    return (target_path / ".git").exists()


def get_commit_hash(target_path: Path) -> str | None:
    try:
        return Repo(target_path).head.commit.hexsha
    except (InvalidGitRepositoryError, ValueError):
        return None


def _remote_branch(repo: Repo, preferred: str | None = None) -> str:
    if preferred:
        try:
            repo.commit(f"origin/{preferred}")
        except (ValueError, BadName):
            pass
        else:
            return preferred
    try:
        return repo.git.symbolic_ref("--short", "refs/remotes/origin/HEAD").split(
            "/", 1
        )[1]
    except Exception:
        refs = [
            ref.remote_head
            for ref in repo.remotes.origin.refs
            if ref.remote_head != "HEAD"
        ]
        if not refs:
            msg = "Origin has no remote branch"
            raise ValueError(msg)
        return refs[0]


def _refresh_remote_head(repo: Repo) -> str | None:
    """Read the remote HEAD and refresh the local origin/HEAD reference."""
    try:
        output = repo.git.ls_remote("--symref", "origin", "HEAD")
        for line in output.splitlines():
            if not line.startswith("ref: refs/heads/"):
                continue
            branch = line.split("\t", 1)[0].removeprefix("ref: refs/heads/")
            repo.git.symbolic_ref(
                "refs/remotes/origin/HEAD",
                f"refs/remotes/origin/{branch}",
            )
            return branch
    except Exception:
        return None
    return None


def fetch_and_checkout(
    target_path: Path,
    *,
    branch: str | None = None,
) -> tuple[str, str]:
    """Fetch origin and check out its branch commit without merging."""
    repo = Repo(target_path)
    repo.remotes.origin.fetch(prune=True)
    resolved_branch = branch or _refresh_remote_head(repo) or _remote_branch(repo)
    commit = repo.commit(f"origin/{resolved_branch}")
    repo.git.checkout("--detach", commit.hexsha)
    return resolved_branch, commit.hexsha


def get_remote_branch(target_path: Path) -> str:
    """Return the branch tracked through origin."""
    return _remote_branch(Repo(target_path))


def commit_exists(target_path: Path, commit: str) -> bool:
    try:
        Repo(target_path).commit(commit)
    except (ValueError, BadName):
        return False
    return True


def parse_diff_z(output: bytes | str) -> list[GitChange]:
    """Parse NUL separated git name status output."""
    raw = (
        output.decode("utf-8", errors="surrogateescape")
        if isinstance(output, bytes)
        else output
    )
    fields = raw.split("\0")
    if fields and not fields[-1]:
        fields.pop()
    changes: list[GitChange] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        code = status[:1]
        if code in {"R", "C"}:
            old_path, path = fields[index : index + 2]
            index += 2
            changes.append(GitChange(code, path, old_path))
        else:
            path = fields[index]
            index += 1
            changes.append(GitChange(code, path))
    return changes


def diff_changes(target_path: Path, old_sha: str, new_sha: str) -> list[GitChange]:
    repo = Repo(target_path)
    output = repo.git.diff(
        "--name-status",
        "-z",
        "--find-renames",
        "--find-copies",
        old_sha,
        new_sha,
    )
    return parse_diff_z(output)


def download(
    repo_url: str,
    target_path: Path,
    *,
    branch: str | None = None,
) -> Path:
    """Clone the repo if missing, then fetch and detach at the remote commit.

    Returns the local path so it plugs directly into
    ``PipelineConfig.root``.
    """
    if is_cloned(target_path):
        repo = Repo(target_path)
        if repo.remotes.origin.url != repo_url:
            repo.remotes.origin.set_url(repo_url)
    else:
        Repo.clone_from(repo_url, target_path)
    fetch_and_checkout(target_path, branch=branch)
    return target_path
