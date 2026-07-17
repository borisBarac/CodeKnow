from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import _GITHUB_RE, _GITHUB_SSH_RE

if TYPE_CHECKING:
    from pathlib import Path

    import networkx as nx

    from codeknow.pipeline.config import PipelineConfig
    from codeknow.schemas import CommunityMap


def resolve(config: PipelineConfig, **kwargs: Any) -> Path:
    """Resolve a GitHub repo URL to a local path.

    Checks the repo map cache first; if not found, clones the repo.
    Returns the local path for use by the ``detect`` stage.
    """
    from codeknow.git_download import download, get_path, is_cloned, register

    match = _GITHUB_RE.match(config.repo_url)
    if not match:
        match = _GITHUB_SSH_RE.match(config.repo_url)
    if not match:
        msg = (
            f"Invalid GitHub URL: {config.repo_url}. "
            "Expected https://github.com/<owner>/<repo>[.git] "
            "or git@github.com:<owner>/<repo>[.git]"
        )
        raise ValueError(msg)

    cached = get_path(config.repo_url)
    if not config.fetch_remote:
        if cached is not None and cached.exists() and is_cloned(cached):
            return cached
        if cached is None:
            recovered = config.resolved_input_dir() / config.slug
            if recovered.exists() and is_cloned(recovered):
                register(config.repo_url, recovered)
                return recovered
        if cached is None or not cached.exists() or not is_cloned(cached):
            msg = (
                f"Cached checkout not found for {config.repo_url}. "
                "Run `codeknow add` before using --no-fetch."
            )
            raise FileNotFoundError(msg)

    if cached is not None and cached.exists():
        return download(config.repo_url, cached)

    target = config.resolved_input_dir() / config.slug
    target.parent.mkdir(parents=True, exist_ok=True)

    local_path = download(config.repo_url, target)
    register(config.repo_url, local_path)
    return local_path


def _assign_communities(G: nx.Graph, communities: CommunityMap) -> None:
    for cid, node_ids in communities.items():
        for nid in node_ids:
            if nid in G.nodes:
                G.nodes[nid]["community"] = cid


def _to_dict(result: Any) -> dict:
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()  # type: ignore[no-any-return]
    return {"nodes": [], "edges": []}
