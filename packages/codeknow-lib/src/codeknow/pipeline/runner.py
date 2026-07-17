from __future__ import annotations

import logging
import shutil
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codeknow.git_download.downloader import (
    commit_exists,
    diff_changes,
    get_commit_hash,
    get_remote_branch,
)
from codeknow.paths import repository_path

from .config import INDEX_SCHEMA_VERSION, PipelineConfig
from .io import (
    cleanup_generations,
    communities_from_graph,
    load_chunk_map,
    load_current,
    load_graph,
    load_metadata,
    new_generation_id,
    save_pipeline_result,
)
from .stages import _assign_communities, _to_dict, resolve
from .types import PipelineResult

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _cleanup_old_generations(config: PipelineConfig) -> None:
    try:
        removed = cleanup_generations(
            config.resolved_output_dir(),
            grace_seconds=config.generation_grace_seconds,
        )
        if config.no_embed:
            return
        from codeknow.vector.chroma import ChromaConfig, delete_collection

        for _generation_id, old_collection in removed:
            if old_collection:
                delete_collection(
                    ChromaConfig(
                        host=config.chroma_host,
                        port=config.chroma_port,
                        collection_name=old_collection,
                    )
                )
    except Exception:
        logger.warning("Old generation cleanup failed", exc_info=True)


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
    managed = isinstance(config, PipelineConfig)
    active = None
    active_metadata = None
    if managed:
        try:
            active = load_current(config.resolved_output_dir())
            active_metadata = load_metadata(config.resolved_output_dir())
        except (FileNotFoundError, KeyError, ValueError):
            logger.warning(
                "Active generation is incomplete; running a full build",
                exc_info=True,
            )

    _resolve = resolve_fn or resolve
    _detect = detect_fn or _extractor.discover
    _extract_ast = extract_ast_fn or _extractor.extract_from_discovery
    _build = build_graph_fn or build
    _map_chunks = map_chunks_fn or _default_map_chunks
    _cluster = cluster_fn or cluster
    _embed = embed_fn or _default_embed

    root = Path(_resolve(config))
    _progress(progress_callback, 0)
    commit_hash = get_commit_hash(root)
    branch_name = get_remote_branch(root) if managed else None

    fingerprint_matches = bool(
        active_metadata
        and active_metadata.get("schema_version") == INDEX_SCHEMA_VERSION
        and active_metadata.get("build_fingerprint") == config.build_fingerprint()
        and active_metadata.get("branch") == branch_name
    )
    old_commit = active_metadata.get("commit_hash") if active_metadata else None
    can_reuse = bool(
        active
        and config.update
        and not config.force_rebuild
        and fingerprint_matches
        and old_commit
        and commit_hash
        and commit_exists(root, old_commit)
    )

    if can_reuse and old_commit == commit_hash and active is not None:
        graph = load_graph(active.directory / config.graph_filename)
        chunk_map = load_chunk_map(active.directory)
        communities = communities_from_graph(graph)
        stats = {
            "nodes": graph.number_of_nodes(),
            "edges": graph.number_of_edges(),
            "communities": len(communities),
            "files": len(chunk_map),
            "words": 0,
        }
        return PipelineResult(
            graph=graph,
            communities=communities,
            chunk_map=chunk_map,
            discovery={},
            stats=stats,
            config=config,
            commit_hash=commit_hash,
            graph_path=active.directory / config.graph_filename,
            repo_root=root,
            generation_id=active.generation_id,
            collection_name=active.collection_name,
            changed_paths=frozenset(),
            branch_name=branch_name,
        )

    if managed:
        _cleanup_old_generations(config)

    raw = _detect(root)
    discovery = raw if isinstance(raw, dict) else _to_dict(raw)
    _progress(progress_callback, 1)

    extractions: list[dict] = []
    ast_result = (
        _extract_ast(discovery, repo_root=root)
        if extract_ast_fn is None
        else _extract_ast(discovery)
    )
    extractions.append(
        ast_result if isinstance(ast_result, dict) else _to_dict(ast_result)
    )
    _progress(progress_callback, 2)

    G = _build(extractions)
    _progress(progress_callback, 3)

    prior_chunk_map = load_chunk_map(active.directory) if can_reuse and active else None
    changed_paths: frozenset[str] | None = None
    if can_reuse and old_commit and commit_hash and prior_chunk_map is not None:
        git_changed: set[str] = set()
        for change in diff_changes(root, old_commit, commit_hash):
            git_changed.add(repository_path(change.path, root))
            if change.old_path is not None:
                git_changed.add(repository_path(change.old_path, root))
        discovered_paths = {
            repository_path(path, root)
            for paths in discovery.get("files", {}).values()
            for path in paths
        }
        old_paths = set(prior_chunk_map)
        changed_paths = frozenset(
            git_changed
            | (discovered_paths - old_paths)
            | (old_paths - discovered_paths)
        )

    if map_chunks_fn is None:
        G, chunk_map = _map_chunks(
            G,
            discovery.get("files", {}),
            chunk_size=config.chunk_size,
            overlap=config.chunk_overlap,
            repo_root=root,
            prior_chunk_map=prior_chunk_map,
            changed_paths=changed_paths,
        )
    else:
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

    generation_id = new_generation_id() if managed else None
    collection_name = (
        f"codeknow_{config.slug}_{generation_id}" if generation_id else None
    )
    result = PipelineResult(
        graph=G,
        communities=communities,
        chunk_map=chunk_map,
        discovery=discovery,
        stats=stats,
        config=config,
        commit_hash=commit_hash,
        repo_root=root,
        generation_id=generation_id,
        collection_name=collection_name,
        changed_paths=changed_paths,
        branch_name=branch_name,
        prior_collection_name=(
            active.collection_name if can_reuse and active is not None else None
        ),
    )

    try:
        result = _embed(result, on_progress=_make_embed_progress(progress_callback))
        _progress(progress_callback, 6)
        graph_path = save_pipeline_result(result)
    except Exception:
        if generation_id is not None:
            shutil.rmtree(
                config.resolved_output_dir() / "generations" / generation_id,
                ignore_errors=True,
            )
        if collection_name is not None and not config.no_embed:
            from codeknow.vector.chroma import ChromaConfig, delete_collection

            delete_collection(
                ChromaConfig(
                    host=config.chroma_host,
                    port=config.chroma_port,
                    collection_name=collection_name,
                )
            )
        raise
    if managed:
        _cleanup_old_generations(config)
    return replace(result, graph_path=graph_path)
