from __future__ import annotations

import logging
import shutil
from dataclasses import replace
from datetime import datetime, timezone
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
        _cleanup_abandoned_collections(config)
    except Exception:
        logger.warning("Old generation cleanup failed", exc_info=True)


def _cleanup_abandoned_collections(config: PipelineConfig) -> None:
    import json

    from codeknow.vector.chroma import (
        ChromaConfig,
        delete_collection,
        list_collection_names,
    )

    base = config.chroma_collection or f"codeknow_{config.slug}"
    chroma = ChromaConfig(
        host=config.chroma_host,
        port=config.chroma_port,
        collection_name=base,
    )
    names = list_collection_names(chroma)
    if names is None:
        return
    protected: set[str] = set()
    current_collection: str | None = None
    pointer = config.resolved_output_dir() / "current.json"
    if pointer.exists():
        data = json.loads(pointer.read_text(encoding="utf-8"))
        for key in ("collection_name", "previous_collection_name"):
            if data.get(key):
                protected.add(data[key])
        current_collection = data.get("collection_name")
    if base in names and current_collection and current_collection != base:
        delete_collection(chroma)
    prefix = f"{base}_"
    cutoff = datetime.now(timezone.utc).timestamp() - config.generation_grace_seconds
    for name in names:
        if name in protected or not name.startswith(prefix):
            continue
        generation_id = name.removeprefix(prefix)
        generation_dir = config.resolved_output_dir() / "generations" / generation_id
        if generation_dir.exists():
            continue
        try:
            stamp = generation_id.split("-", 1)[0]
            created = datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if created.timestamp() > cutoff:
            continue
        delete_collection(chroma.model_copy(update={"collection_name": name}))


def _run_pipeline_unlocked(
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

    managed = isinstance(config, PipelineConfig)
    _extractor = Extractor(use_cache=not (managed and config.force_rebuild))
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
    legacy_collection = (
        (config.chroma_collection or f"codeknow_{config.slug}")
        if active is None and active_metadata is not None
        else None
    )

    _resolve = resolve_fn or resolve
    _detect = detect_fn or _extractor.discover
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
    active_graph = None
    prior_chunk_map = None
    if (
        can_reuse
        and not config.no_embed
        and active is not None
        and active_metadata is not None
    ):
        from codeknow.pipeline.metadata import build_vector_metadata
        from codeknow.schemas import vector_ids_digest
        from codeknow.vector.chroma import (
            ChromaConfig,
            validate_collection_records,
        )

        try:
            active_graph = load_graph(active.directory / active.graph_filename)
            prior_chunk_map = load_chunk_map(
                active.directory / active.chunk_map_filename
            )
            active_communities = communities_from_graph(active_graph)
            prior_result = PipelineResult(
                graph=active_graph,
                communities=active_communities,
                chunk_map=prior_chunk_map,
                discovery={},
                stats={},
                config=config,
                commit_hash=old_commit,
                repo_root=root,
            )
            all_metadata = build_vector_metadata(
                prior_result,
                check_content=False,
            )
            stored_ids = active_metadata.get("vector_ids")
            if not isinstance(stored_ids, list) or any(
                not isinstance(item, str) for item in stored_ids
            ):
                expected_metadata = {}
                can_reuse = False
            else:
                expected_metadata = all_metadata
                if set(stored_ids) != set(expected_metadata):
                    can_reuse = False
        except (FileNotFoundError, KeyError, ValueError):
            expected_metadata = {}
            can_reuse = False
        chroma_config = ChromaConfig(
            host=config.chroma_host,
            port=config.chroma_port,
            collection_name=active.collection_name,
        )
        vector_count = active_metadata.get("vector_count")
        expected_digest = active_metadata.get("vector_ids_digest")
        expected_ids = set(expected_metadata)
        if can_reuse and (
            vector_count != len(expected_ids)
            or expected_digest != vector_ids_digest(expected_ids)
            or not validate_collection_records(chroma_config, expected_metadata)
        ):
            logger.warning("Active vector collection is incomplete; rebuilding")
            can_reuse = False

    if can_reuse and old_commit == commit_hash and active is not None:
        graph = active_graph or load_graph(active.directory / active.graph_filename)
        chunk_map = prior_chunk_map or load_chunk_map(
            active.directory / active.chunk_map_filename
        )
        if config.no_embed or active_graph is not None:
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
                graph_path=active.directory / active.graph_filename,
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
    if extract_ast_fn is None:
        ast_result = _extractor.extract_from_discovery(
            discovery,
            repo_root=root,
        )
    else:
        ast_result = extract_ast_fn(discovery)
    extractions.append(
        ast_result if isinstance(ast_result, dict) else _to_dict(ast_result)
    )
    _progress(progress_callback, 2)

    G = _build(extractions)
    _progress(progress_callback, 3)

    if prior_chunk_map is None and can_reuse and active:
        prior_chunk_map = load_chunk_map(active.directory / active.chunk_map_filename)
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
    collection_base = config.chroma_collection or f"codeknow_{config.slug}"
    collection_name = f"{collection_base}_{generation_id}" if generation_id else None
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
        if legacy_collection is not None and not config.no_embed:
            from codeknow.vector.chroma import ChromaConfig, delete_collection

            delete_collection(
                ChromaConfig(
                    host=config.chroma_host,
                    port=config.chroma_port,
                    collection_name=legacy_collection,
                )
            )
        _cleanup_old_generations(config)
    return replace(result, graph_path=graph_path)


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
    """Run one pipeline build while holding the repository slug lock."""
    from codeknow.pipeline.locking import slug_build_lock

    output_dir = config.resolved_output_dir()
    with slug_build_lock(output_dir.parent, config.slug):
        return _run_pipeline_unlocked(
            config,
            resolve_fn=resolve_fn,
            detect_fn=detect_fn,
            extract_ast_fn=extract_ast_fn,
            build_graph_fn=build_graph_fn,
            map_chunks_fn=map_chunks_fn,
            cluster_fn=cluster_fn,
            embed_fn=embed_fn,
            progress_callback=progress_callback,
            **kwargs,
        )
