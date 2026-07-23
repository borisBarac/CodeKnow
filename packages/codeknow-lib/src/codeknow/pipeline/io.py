from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from networkx.readwrite import json_graph as _jg

from codeknow.pipeline.config import INDEX_SCHEMA_VERSION

if TYPE_CHECKING:
    from pathlib import Path

    import networkx as nx

    from codeknow.pipeline.types import PipelineResult


@dataclass(frozen=True)
class GenerationRef:
    generation_id: str
    collection_name: str
    directory: Path
    graph_filename: str = "graph.json"
    chunk_map_filename: str = "chunk_map.json"


def new_generation_id() -> str:
    """Return a sortable unique generation ID."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{uuid4().hex[:8]}"


def load_current(output_dir: Path) -> GenerationRef | None:
    """Read and validate the active generation pointer once."""
    pointer = output_dir / "current.json"
    if not pointer.exists():
        return None
    data = json.loads(pointer.read_text(encoding="utf-8"))
    generation_id = data["generation_id"]
    collection_name = data["collection_name"]
    directory = output_dir / "generations" / generation_id
    ref = GenerationRef(
        generation_id,
        collection_name,
        directory,
        data.get("graph_filename", "graph.json"),
        data.get("chunk_map_filename", "chunk_map.json"),
    )
    validate_generation(ref)
    return ref


def validate_generation(ref: GenerationRef) -> None:
    """Raise when any required generation file is missing or invalid."""
    for filename in (
        ref.graph_filename,
        ref.chunk_map_filename,
        "metadata.json",
    ):
        path = ref.directory / filename
        if not path.is_file():
            msg = f"Incomplete generation {ref.generation_id}: missing {filename}"
            raise FileNotFoundError(msg)
        json.loads(path.read_text(encoding="utf-8"))


def load_metadata(output_dir: Path) -> dict | None:
    current = load_current(output_dir)
    path = (
        current.directory / "metadata.json"
        if current is not None
        else output_dir / "metadata.json"
    )
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def save_metadata(result: PipelineResult) -> Path:
    cfg = result.config
    out = _result_directory(result)
    metadata = {
        "github_ssh_url": cfg.repo_url,
        "slug": cfg.slug,
        "commit_hash": result.commit_hash,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "node_count": result.graph.number_of_nodes(),
        "edge_count": result.graph.number_of_edges(),
        "community_count": len(result.communities),
        "schema_version": INDEX_SCHEMA_VERSION,
        "build_fingerprint": cfg.build_fingerprint(),
        "branch": result.branch_name,
        "generation_id": result.generation_id,
        "collection_name": result.collection_name,
        "graph_filename": cfg.graph_filename,
        "chunk_map_filename": cfg.chunk_map_filename,
    }
    if result.embed_stats is not None:
        vector_ids = result.embed_stats.get("vector_ids", [])
        metadata["vector_count"] = len(vector_ids)
        metadata["vector_ids_digest"] = result.embed_stats.get("vector_ids_digest")
        metadata["vector_ids"] = vector_ids
    path = out / "metadata.json"
    path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_chunk_map(path: Path) -> dict:
    """Load a chunk map from a generation directory or file."""
    from codeknow.schemas import Chunk

    source = path / "chunk_map.json" if path.is_dir() else path
    data = json.loads(source.read_text(encoding="utf-8"))
    return {
        file: [Chunk.model_validate(chunk) for chunk in chunks]
        for file, chunks in data.items()
    }


def _result_directory(result: PipelineResult) -> Path:
    out = result.config.resolved_output_dir()
    if result.generation_id is None:
        return out
    return out / "generations" / result.generation_id


def publish_generation(output_dir: Path, ref: GenerationRef) -> None:
    """Atomically replace the active generation pointer."""
    validate_generation(ref)
    output_dir.mkdir(parents=True, exist_ok=True)
    pointer = output_dir / "current.json"
    previous: dict[str, str] = {}
    if pointer.exists():
        try:
            old = json.loads(pointer.read_text(encoding="utf-8"))
            old_ref = GenerationRef(
                old["generation_id"],
                old["collection_name"],
                output_dir / "generations" / old["generation_id"],
                old.get("graph_filename", "graph.json"),
                old.get("chunk_map_filename", "chunk_map.json"),
            )
            validate_generation(old_ref)
            previous = {
                "previous_generation_id": old["generation_id"],
                "previous_collection_name": old["collection_name"],
            }
            retired_at = old_ref.directory / "retired_at"
            retired_temp = old_ref.directory / f".retired-{uuid4().hex}.tmp"
            retired_temp.write_text(
                datetime.now(timezone.utc).isoformat(),
                encoding="utf-8",
            )
            retired_temp.replace(retired_at)
        except (json.JSONDecodeError, KeyError, FileNotFoundError, ValueError):
            previous = {}
    temp = output_dir / f".current-{uuid4().hex}.tmp"
    pointer_data = {
        "generation_id": ref.generation_id,
        "collection_name": ref.collection_name,
        **previous,
    }
    if ref.graph_filename != "graph.json":
        pointer_data["graph_filename"] = ref.graph_filename
    if ref.chunk_map_filename != "chunk_map.json":
        pointer_data["chunk_map_filename"] = ref.chunk_map_filename
    temp.write_text(
        json.dumps(
            pointer_data,
            indent=2,
        ),
        encoding="utf-8",
    )
    temp.replace(pointer)


def cleanup_generations(
    output_dir: Path,
    *,
    grace_seconds: int,
    keep: int = 2,
) -> list[tuple[str, str | None]]:
    """Remove expired generations except the newest retained generations."""
    current = load_current(output_dir)
    pointer_data: dict = {}
    pointer = output_dir / "current.json"
    if pointer.exists():
        pointer_data = json.loads(pointer.read_text(encoding="utf-8"))
    generations_dir = output_dir / "generations"
    if not generations_dir.is_dir():
        return []
    directories = sorted(
        (path for path in generations_dir.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    if keep < 1:
        msg = "keep must be at least one"
        raise ValueError(msg)
    protected: set[str] = set()
    if current is not None:
        protected.add(current.generation_id)
    previous_id = pointer_data.get("previous_generation_id")
    if previous_id:
        protected.add(previous_id)
    cutoff = time.time() - grace_seconds
    removed: list[tuple[str, str | None]] = []
    for directory in directories:
        if directory.name in protected:
            continue
        collection_name: str | None = None
        metadata_path = directory / "metadata.json"
        complete = True
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            filenames = (
                metadata.get("graph_filename", "graph.json"),
                metadata.get("chunk_map_filename", "chunk_map.json"),
                "metadata.json",
            )
            for filename in filenames:
                json.loads((directory / filename).read_text(encoding="utf-8"))
            collection_name = metadata.get("collection_name")
        except (FileNotFoundError, json.JSONDecodeError):
            complete = False
        if complete:
            retired_at = directory / "retired_at"
            if not retired_at.exists():
                retired_at.write_text(
                    datetime.now(timezone.utc).isoformat(),
                    encoding="utf-8",
                )
                continue
            try:
                retired_time = datetime.fromisoformat(
                    retired_at.read_text(encoding="utf-8")
                ).timestamp()
            except (OSError, ValueError):
                retired_at.write_text(
                    datetime.now(timezone.utc).isoformat(),
                    encoding="utf-8",
                )
                continue
            if retired_time > cutoff:
                continue
        shutil.rmtree(directory)
        removed.append((directory.name, collection_name))
    return removed


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
    out = _result_directory(result)
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

    save_metadata(result)

    if result.generation_id is not None and result.collection_name is not None:
        ref = GenerationRef(
            result.generation_id,
            result.collection_name,
            out,
            cfg.graph_filename,
            cfg.chunk_map_filename,
        )
        publish_generation(cfg.resolved_output_dir(), ref)

    return graph_path
