from __future__ import annotations

import json
from typing import TYPE_CHECKING

from networkx.readwrite import json_graph as _jg

if TYPE_CHECKING:
    from pathlib import Path

    import networkx as nx

    from codeknow.pipeline.types import PipelineResult


def load_graph(path: Path) -> nx.Graph:
    resolved = path.resolve()
    if resolved.suffix != ".json":
        msg = f"Graph path must be a .json file, got: {resolved!r}"
        raise ValueError(msg)
    if not resolved.exists():
        msg = f"Graph file not found: {resolved}"
        raise FileNotFoundError(msg)
    data = json.loads(resolved.read_text(encoding="utf-8"))
    try:
        return _jg.node_link_graph(data, edges="links")  # type: ignore[no-any-return]
    except TypeError:
        return _jg.node_link_graph(data)  # type: ignore[no-any-return]


def communities_from_graph(G: nx.Graph) -> dict[int, list[str]]:
    communities: dict[int, list[str]] = {}
    for node_id, ndata in G.nodes(data=True):
        cid = ndata.get("community")
        if cid is not None:
            communities.setdefault(int(cid), []).append(node_id)
    return communities


def save_pipeline_result(
    result: PipelineResult,
) -> Path:
    """Serialize pipeline outputs to disk.

    Writes:
    - ``<graph_filename>`` — NetworkX graph in node-link format
    - ``<chunk_map_filename>`` — file → [chunks] mapping
    - ``embed_stats.json`` — embedding stats (if available)

    Output paths are read from ``result.config``.
    Returns the resolved path to the saved graph file.
    """
    cfg = result.config
    out = cfg.resolved_output_dir()
    out.mkdir(parents=True, exist_ok=True)

    graph_data = _jg.node_link_data(result.graph, edges="links")
    graph_path = (out / cfg.graph_filename).resolve()
    graph_path.write_text(
        json.dumps(graph_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    chunk_data = {
        fpath: [chunk.model_dump() for chunk in chunks]
        for fpath, chunks in result.chunk_map.items()
    }
    (out / cfg.chunk_map_filename).write_text(
        json.dumps(chunk_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if result.embed_stats is not None:
        (out / "embed_stats.json").write_text(
            json.dumps(result.embed_stats, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return graph_path
