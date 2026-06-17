from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from codeknow.git_download.downloader import get_commit_hash

from .io import save_pipeline_result
from .stages import _assign_communities, _to_dict, resolve
from .types import PipelineResult

if TYPE_CHECKING:
    from collections.abc import Callable

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

# Embeddings are typically the longest stage, so they own the entire second
# half of the progress bar (50 -> 100). The first six stages share 0 -> 50.
_STAGES: list[tuple[str, int, str]] = [
    ("resolve", 7, "Resolving repository..."),
    ("detect", 14, "Discovering files..."),
    ("extract_ast", 21, "Extracting AST..."),
    ("build", 28, "Building graph..."),
    ("map_chunks", 35, "Mapping chunks..."),
    ("cluster", 50, "Detecting communities..."),
    ("embed", 100, "Generating embeddings..."),
]


def _progress(
    progress_callback: Callable[[str, int, str], None] | None,
    stage_index: int,
) -> None:
    if progress_callback is None:
        return
    stage, pct, msg = _STAGES[stage_index]
    progress_callback(stage, pct, msg)


def _make_embed_progress(
    progress_callback: Callable[[str, int, str], None] | None,
) -> Callable[[int, int], None] | None:
    """Adapt the store's raw ``(done, total)`` counts into a callback that
    reports the ``embed`` stage across the ``[cluster_pct, embed_pct]`` window.

    The store only knows how many chunks it has stored; this maps that fraction
    onto the percentage range reserved for embeddings (50 -> 100 by default).
    """
    if progress_callback is None:
        return None
    lo_pct = _STAGES[5][1]  # cluster's terminal pct (start of embed window)
    hi_pct = _STAGES[6][1]  # embed's terminal pct (end of embed window)
    stage, _pct, msg = _STAGES[6]

    def _on_embed(done: int, total: int) -> None:
        if total <= 0:
            return
        frac = done / total
        pct = min(hi_pct, round(lo_pct + (hi_pct - lo_pct) * frac))
        progress_callback(stage, pct, msg)

    return _on_embed


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
    progress_callback: Callable[[str, int, str], None] | None = None,
    **kwargs: Any,
) -> PipelineResult:
    """Execute: resolve → detect → extract → build → map_chunks → cluster → embed.

    Each ``*_fn`` argument overrides the default implementation.
    Stubs are used for stages not yet implemented.
    """
    from codeknow.extract.extractor import Extractor
    from codeknow.graph.build import build
    from codeknow.graph.cluster import cluster
    from codeknow.pipeline.chunk_stage import map_chunks as _default_map_chunks
    from codeknow.pipeline.embed_stage import embed as _default_embed

    _extractor = Extractor()

    _resolve = resolve_fn or resolve
    _detect = detect_fn or _extractor.discover
    _extract_ast = extract_ast_fn or _extractor.extract_from_discovery
    _build = build_graph_fn or build
    _map_chunks = map_chunks_fn or _default_map_chunks
    _cluster = cluster_fn or cluster
    _embed = embed_fn or _default_embed

    root = _resolve(config)
    _progress(progress_callback, 0)
    commit_hash = get_commit_hash(root)

    raw = _detect(root)
    discovery = raw if isinstance(raw, dict) else _to_dict(raw)
    _progress(progress_callback, 1)

    extractions: list[dict] = []
    ast_result = _extract_ast(discovery)
    extractions.append(
        ast_result if isinstance(ast_result, dict) else _to_dict(ast_result)
    )
    _progress(progress_callback, 2)

    G = _build(extractions)
    _progress(progress_callback, 3)

    G, chunk_map = _map_chunks(G, discovery.get("files", {}))
    _progress(progress_callback, 4)

    communities = _cluster(G)
    _assign_communities(G, communities)
    _progress(progress_callback, 5)

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

    result = _embed(result, on_progress=_make_embed_progress(progress_callback))
    _progress(progress_callback, 6)

    graph_path = save_pipeline_result(result)
    return replace(result, graph_path=graph_path, commit_hash=commit_hash)
