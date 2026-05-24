from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from codeknow.git_download.downloader import get_commit_hash

from .io import save_pipeline_result
from .stages import _assign_communities, _to_dict, resolve
from .types import PipelineResult

if TYPE_CHECKING:
    from codeknow.pipeline.config import PipelineConfig
    from codeknow.pipeline.types import (
        BuildGraphFn,
        ClusterFn,
        DetectFn,
        EmbedFn,
        ExtractAstFn,
        MapChunksFn,
        ResolveFn,
    )


def run_pipeline(
    config: PipelineConfig,
    *,
    resolve_fn: ResolveFn | None = None,
    detect_fn: DetectFn | None = None,
    extract_ast_fn: ExtractAstFn | None = None,
    build_graph_fn: BuildGraphFn | None = None,
    map_chunks_fn: MapChunksFn | None = None,
    cluster_fn: ClusterFn | None = None,
    embed_fn: EmbedFn | None = None,
    **kwargs: Any,
) -> PipelineResult:
    """Execute: resolve → detect → extract → build → map_chunks → cluster → embed.

    Each ``*_fn`` argument overrides the default implementation.
    Stubs are used for stages not yet implemented.
    """
    from codeknow.extract.ast import extract_ast
    from codeknow.extract.detect import detect
    from codeknow.graph.build import build
    from codeknow.graph.cluster import cluster
    from codeknow.pipeline.chunk_stage import map_chunks as _default_map_chunks
    from codeknow.pipeline.embed_stage import embed as _default_embed

    _resolve = resolve_fn or resolve
    _detect = detect_fn or detect
    _extract_ast = extract_ast_fn or extract_ast
    _build = build_graph_fn or build
    _map_chunks = map_chunks_fn or _default_map_chunks
    _cluster = cluster_fn or cluster
    _embed = embed_fn or _default_embed

    root = _resolve(config)
    commit_hash = get_commit_hash(root)

    raw = _detect(root)
    discovery = raw if isinstance(raw, dict) else _to_dict(raw)

    extractions: list[dict] = []
    ast_result = _extract_ast(discovery.get("files", {}))
    extractions.append(
        ast_result if isinstance(ast_result, dict) else _to_dict(ast_result)
    )

    G = _build(extractions)

    G, chunk_map = _map_chunks(G, discovery.get("files", {}))

    communities = _cluster(G)
    _assign_communities(G, communities)

    stats = {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "communities": len(communities),
        "files": discovery.get("total_files", 0),
        "words": discovery.get("total_words", 0),
    }

    result = PipelineResult(
        graph=G,
        communities=communities,
        chunk_map=chunk_map,
        discovery=discovery,
        stats=stats,
        config=config,
    )

    result = _embed(result)
    graph_path = save_pipeline_result(result)
    return replace(result, graph_path=graph_path, commit_hash=commit_hash)
