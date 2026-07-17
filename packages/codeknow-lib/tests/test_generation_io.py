from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest
from codeknow.pipeline.config import PipelineConfig
from codeknow.pipeline.io import (
    GenerationRef,
    cleanup_generations,
    load_current,
    publish_generation,
)
from codeknow.pipeline.runner import _cleanup_abandoned_collections
from codeknow.vector.search import GraphSearcher
from networkx.readwrite import json_graph

if TYPE_CHECKING:
    from pathlib import Path


def _generation(
    output_dir: Path,
    generation_id: str,
    *,
    collection_name: str | None = None,
) -> GenerationRef:
    directory = output_dir / "generations" / generation_id
    directory.mkdir(parents=True)
    graph = nx.Graph()
    graph.add_node(
        generation_id,
        label=generation_id,
        chunks=[{"hash": "a" * 64, "vector_id": "b" * 64}],
    )
    (directory / "graph.json").write_text(
        json.dumps(json_graph.node_link_data(graph, edges="links")),
        encoding="utf-8",
    )
    (directory / "chunk_map.json").write_text("{}", encoding="utf-8")
    collection = collection_name or f"collection-{generation_id}"
    (directory / "metadata.json").write_text(
        json.dumps({"collection_name": collection}),
        encoding="utf-8",
    )
    return GenerationRef(generation_id, collection, directory)


def test_publish_and_load_current_generation(tmp_path: Path) -> None:
    ref = _generation(tmp_path, "generation-1")

    publish_generation(tmp_path, ref)

    assert load_current(tmp_path) == ref
    assert json.loads((tmp_path / "current.json").read_text()) == {
        "generation_id": "generation-1",
        "collection_name": "collection-generation-1",
    }
    assert list(tmp_path.glob(".current-*.tmp")) == []


def test_publish_switches_generation_and_records_previous(tmp_path: Path) -> None:
    first = _generation(tmp_path, "generation-1")
    second = _generation(tmp_path, "generation-2")
    publish_generation(tmp_path, first)

    publish_generation(tmp_path, second)

    assert load_current(tmp_path) == second
    assert json.loads((tmp_path / "current.json").read_text()) == {
        "generation_id": "generation-2",
        "collection_name": "collection-generation-2",
        "previous_generation_id": "generation-1",
        "previous_collection_name": "collection-generation-1",
    }


def test_failed_publish_keeps_old_pointer(tmp_path: Path) -> None:
    current = _generation(tmp_path, "generation-1")
    publish_generation(tmp_path, current)
    pointer_before = (tmp_path / "current.json").read_bytes()
    incomplete_dir = tmp_path / "generations" / "generation-2"
    incomplete_dir.mkdir(parents=True)
    incomplete = GenerationRef("generation-2", "collection-2", incomplete_dir)

    with pytest.raises(FileNotFoundError, match="Incomplete generation"):
        publish_generation(tmp_path, incomplete)

    assert (tmp_path / "current.json").read_bytes() == pointer_before
    assert load_current(tmp_path) == current


def test_cleanup_removes_only_expired_unprotected_generations(
    tmp_path: Path,
) -> None:
    expired = _generation(tmp_path, "generation-1")
    previous = _generation(tmp_path, "generation-2")
    current = _generation(tmp_path, "generation-3")
    publish_generation(tmp_path, previous)
    publish_generation(tmp_path, current)
    old_time = time.time() - 120
    os.utime(expired.directory, (old_time, old_time))
    os.utime(previous.directory, (old_time, old_time))
    os.utime(current.directory, (old_time, old_time))

    removed = cleanup_generations(tmp_path, grace_seconds=60, keep=1)

    assert removed == [(expired.generation_id, expired.collection_name)]
    assert not expired.directory.exists()
    assert previous.directory.exists()
    assert current.directory.exists()


def test_cleanup_keeps_unexpired_abandoned_generation(tmp_path: Path) -> None:
    current = _generation(tmp_path, "generation-2")
    abandoned = _generation(tmp_path, "generation-1")
    publish_generation(tmp_path, current)

    assert cleanup_generations(tmp_path, grace_seconds=60, keep=1) == []
    assert abandoned.directory.exists()


def test_searcher_loads_active_graph_and_collection(tmp_path: Path) -> None:
    inactive = _generation(tmp_path, "generation-1", collection_name="old")
    active = _generation(tmp_path, "generation-2", collection_name="active")
    publish_generation(tmp_path, inactive)
    publish_generation(tmp_path, active)

    searcher = GraphSearcher(tmp_path, store=MagicMock())

    assert searcher._collection_name == "active"
    assert searcher._graph is not None
    assert set(searcher._graph.nodes) == {"generation-2"}


def test_search_discovery_accepts_generation_managed_repo(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    legacy = tmp_path / "legacy"
    ignored = tmp_path / "ignored"
    managed.mkdir()
    legacy.mkdir()
    ignored.mkdir()
    (managed / "current.json").write_text("{}", encoding="utf-8")
    (legacy / "metadata.json").write_text("{}", encoding="utf-8")

    assert GraphSearcher._discover_graph_dirs(tmp_path) == [
        ("legacy", legacy),
        ("managed", managed),
    ]


def test_custom_generation_filenames_publish_and_load(tmp_path: Path) -> None:
    directory = tmp_path / "generations" / "custom"
    directory.mkdir(parents=True)
    (directory / "custom-graph.json").write_text(
        json.dumps(json_graph.node_link_data(nx.Graph(), edges="links")),
        encoding="utf-8",
    )
    (directory / "custom-chunks.json").write_text("{}", encoding="utf-8")
    (directory / "metadata.json").write_text("{}", encoding="utf-8")
    ref = GenerationRef(
        "custom",
        "custom-collection",
        directory,
        "custom-graph.json",
        "custom-chunks.json",
    )

    publish_generation(tmp_path, ref)

    assert load_current(tmp_path) == ref
    searcher = GraphSearcher(tmp_path, store=MagicMock())
    assert searcher._graph is not None


def test_cleanup_removes_expired_crashed_staging_collection(tmp_path: Path) -> None:
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=tmp_path,
        generation_grace_seconds=60,
    )
    active = "codeknow_owner-repo_20990101T000000000000Z-active"
    expired = "codeknow_owner-repo_20000101T000000000000Z-expired"
    fresh = "codeknow_owner-repo_20990101T000000000000Z-fresh"
    (tmp_path / "current.json").write_text(
        json.dumps({"generation_id": "active", "collection_name": active}),
        encoding="utf-8",
    )

    with (
        patch(
            "codeknow.vector.chroma.list_collection_names",
            return_value={active, expired, fresh},
        ),
        patch("codeknow.vector.chroma.delete_collection") as delete,
    ):
        _cleanup_abandoned_collections(config)

    assert delete.call_count == 1
    assert delete.call_args.args[0].collection_name == expired
