"""Persistent mapping of git repo URLs to local folder paths."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_STORE_PATH = Path.home() / ".codeknow" / "repo_map.json"


def load(*, store_path: Path = DEFAULT_STORE_PATH) -> dict[str, str]:
    """Load the URL→path mapping from disk. Returns empty dict if file missing."""
    try:
        return json.loads(store_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(mapping: dict[str, str], *, store_path: Path = DEFAULT_STORE_PATH) -> None:
    """Persist the mapping to disk."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def register(
    repo_url: str,
    local_path: Path,
    *,
    store_path: Path = DEFAULT_STORE_PATH,
) -> dict[str, str]:
    """Add or update a URL→path entry and persist. Returns the updated mapping."""
    mapping = load(store_path=store_path)
    mapping[repo_url] = str(local_path.resolve())
    save(mapping, store_path=store_path)
    return mapping


def get_path(repo_url: str, *, store_path: Path = DEFAULT_STORE_PATH) -> Path | None:
    """Look up the local folder for a repo URL. Returns None if not found."""
    mapping = load(store_path=store_path)
    raw = mapping.get(repo_url)
    return Path(raw) if raw else None


def get_url(local_path: Path, *, store_path: Path = DEFAULT_STORE_PATH) -> str | None:
    """Look up the repo URL for a local folder. Returns None if not found."""
    mapping = load(store_path=store_path)
    resolved = str(local_path.resolve())
    for url, path_str in mapping.items():
        if path_str == resolved:
            return url
    return None


def list_all(*, store_path: Path = DEFAULT_STORE_PATH) -> dict[str, str]:
    """Return the full {repo_url: local_path_str} mapping."""
    return load(store_path=store_path)
