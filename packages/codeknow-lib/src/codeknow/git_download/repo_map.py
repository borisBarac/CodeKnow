"""Persistent mapping of git repo URLs to local folder paths."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


def _read_mapping(store_path: Path) -> dict[str, str]:
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"repo map must contain a JSON object, got {type(raw).__name__}"
        raise TypeError(msg)
    return {str(k): str(v) for k, v in raw.items()}

DEFAULT_STORE_PATH = Path.home() / ".codeknow" / "repo_map.json"


def load(*, store_path: Path = DEFAULT_STORE_PATH) -> dict[str, str]:
    """Load the URL→path mapping from disk. Returns empty dict if file missing."""
    try:
        return _read_mapping(store_path)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        msg = f"Corrupt repo map JSON at {store_path}: {exc}"
        raise ValueError(msg) from exc


def save(mapping: dict[str, str], *, store_path: Path = DEFAULT_STORE_PATH) -> None:
    """Persist the mapping to disk."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(mapping, indent=2, ensure_ascii=False)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=store_path.parent,
        prefix=f".{store_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(payload)
    tmp_path.replace(store_path)


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


def unregister(
    repo_url: str,
    *,
    store_path: Path = DEFAULT_STORE_PATH,
) -> dict[str, str]:
    mapping = load(store_path=store_path)
    mapping.pop(repo_url, None)
    save(mapping, store_path=store_path)
    return mapping


def list_all(*, store_path: Path = DEFAULT_STORE_PATH) -> dict[str, str]:
    """Return the full {repo_url: local_path_str} mapping."""
    return load(store_path=store_path)
