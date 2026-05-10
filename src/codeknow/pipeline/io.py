from __future__ import annotations

import json
from typing import TYPE_CHECKING

from networkx.readwrite import json_graph as _jg

if TYPE_CHECKING:
    from codeknow.pipeline.types import PipelineResult


def save_pipeline_result(
    result: PipelineResult,
) -> None:
    """Serialize pipeline outputs to disk.

    Writes:
    - ``<graph_filename>`` — NetworkX graph in node-link format
    - ``<chunk_map_filename>`` — file → [chunks] mapping
    - ``embed_stats.json`` — embedding stats (if available)

    Output paths are read from ``result.config``.
    """
    cfg = result.config
    out = cfg.resolved_output_dir()
    out.mkdir(parents=True, exist_ok=True)

    graph_data = _jg.node_link_data(result.graph, edges="links")
    (out / cfg.graph_filename).write_text(
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
