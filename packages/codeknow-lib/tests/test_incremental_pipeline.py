from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest
from codeknow.git_download import GitChange
from codeknow.pipeline import PipelineConfig, load_current, run_pipeline
from codeknow.schemas import vector_ids_digest

if TYPE_CHECKING:
    from pathlib import Path


def _discovery(*paths: Path) -> dict:
    return {
        "files": {"code": [str(path) for path in paths]},
        "total_files": len(paths),
        "total_words": len(paths),
    }


def _extraction(labels: dict[str, list[str]]) -> dict:
    nodes = []
    for file, file_labels in labels.items():
        for line, label in enumerate(file_labels, start=1):
            nodes.append(
                {
                    "id": f"{file}:{label}",
                    "label": label,
                    "source_file": file,
                    "source_location": f"L{line}",
                    "end_line": line,
                }
            )
    return {"nodes": nodes, "edges": []}


def _build(extractions: list[dict]) -> nx.Graph:
    graph = nx.Graph()
    for node in extractions[0]["nodes"]:
        graph.add_node(node["id"], **node)
    return graph


def _run(
    config: PipelineConfig,
    root: Path,
    discovery: dict,
    extraction: dict,
    *,
    embed_fn=None,
    build_fn=_build,
):
    embed = embed_fn or (lambda result, **_kwargs: result)
    return run_pipeline(
        config,
        resolve_fn=lambda _config: root,
        detect_fn=lambda _root: discovery,
        extract_ast_fn=lambda _discovery: extraction,
        build_graph_fn=build_fn,
        cluster_fn=lambda graph: {0: list(graph.nodes)},
        embed_fn=embed,
    )


def test_incremental_build_reuses_unchanged_chunks_and_removes_stale_nodes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    first = root / "first.py"
    second = root / "second.py"
    first.write_text("old\nremoved\n", encoding="utf-8")
    second.write_text("stable\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="old"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        initial = _run(
            config,
            root,
            _discovery(first, second),
            _extraction({"first.py": ["old", "removed"], "second.py": ["stable"]}),
        )

    previous = load_current(output)
    assert previous is not None
    metadata = json.loads((previous.directory / "metadata.json").read_text())
    assert metadata["commit_hash"] == "old"
    unchanged_chunks = initial.chunk_map["second.py"]
    first.write_text("new\n", encoding="utf-8")
    embed_spy = MagicMock(side_effect=lambda result, **_kwargs: result)

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="new"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch(
            "codeknow.pipeline.runner.diff_changes",
            return_value=[GitChange("M", "first.py")],
        ),
    ):
        updated = _run(
            config,
            root,
            _discovery(first, second),
            _extraction({"first.py": ["new"], "second.py": ["stable"]}),
            embed_fn=embed_spy,
        )

    assert updated.changed_paths == frozenset({"first.py"})
    assert updated.chunk_map["second.py"] == unchanged_chunks
    assert "first.py:removed" not in updated.graph
    assert updated.prior_collection_name == previous.collection_name
    current = load_current(output)
    assert current is not None
    assert current.generation_id != previous.generation_id
    assert embed_spy.call_count == 1


def test_discovery_changes_add_and_remove_files_authoritatively(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    stable = root / "stable.py"
    removed = root / "removed.py"
    added = root / "added.py"
    stable.write_text("stable\n", encoding="utf-8")
    removed.write_text("removed\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="old"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        _run(
            config,
            root,
            _discovery(stable, removed),
            _extraction({"stable.py": ["stable"], "removed.py": ["removed"]}),
        )

    removed.unlink()
    added.write_text("added\n", encoding="utf-8")
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="new"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch(
            "codeknow.pipeline.runner.diff_changes",
            return_value=[GitChange("M", ".graphignore")],
        ),
    ):
        result = _run(
            config,
            root,
            _discovery(stable, added),
            _extraction({"stable.py": ["stable"], "added.py": ["added"]}),
        )

    assert set(result.chunk_map) == {"stable.py", "added.py"}
    assert result.changed_paths == frozenset({".graphignore", "removed.py", "added.py"})


def test_rename_drops_old_path_and_adds_new_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    old = root / "old.py"
    new = root / "new.py"
    old.write_text("value\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="old"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        _run(config, root, _discovery(old), _extraction({"old.py": ["value"]}))

    old.rename(new)
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="new"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch(
            "codeknow.pipeline.runner.diff_changes",
            return_value=[GitChange("R", "new.py", "old.py")],
        ),
    ):
        result = _run(
            config,
            root,
            _discovery(new),
            _extraction({"new.py": ["value"]}),
        )

    assert set(result.chunk_map) == {"new.py"}
    assert result.changed_paths == frozenset({"old.py", "new.py"})


def test_changed_import_rebuilds_edges_to_unchanged_files(tmp_path: Path) -> None:
    def build_with_edges(extractions: list[dict]) -> nx.Graph:
        graph = _build(extractions)
        for edge in extractions[0]["edges"]:
            graph.add_edge(edge["source"], edge["target"], relation=edge["relation"])
        return graph

    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    paths = [root / name for name in ("a.py", "b.py", "c.py")]
    for path in paths:
        path.write_text(path.stem, encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    initial = _extraction({"a.py": ["A"], "b.py": ["B"], "c.py": ["C"]})
    initial["edges"] = [{"source": "a.py:A", "target": "b.py:B", "relation": "imports"}]
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="old"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        _run(
            config,
            root,
            _discovery(*paths),
            initial,
            build_fn=build_with_edges,
        )

    updated = _extraction({"a.py": ["A"], "b.py": ["B"], "c.py": ["C"]})
    updated["edges"] = [{"source": "a.py:A", "target": "c.py:C", "relation": "imports"}]
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="new"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch(
            "codeknow.pipeline.runner.diff_changes",
            return_value=[GitChange("M", "a.py")],
        ),
    ):
        result = _run(
            config,
            root,
            _discovery(*paths),
            updated,
            build_fn=build_with_edges,
        )

    assert result.graph.has_edge("a.py:A", "c.py:C")
    assert not result.graph.has_edge("a.py:A", "b.py:B")


def test_same_commit_returns_active_generation_without_pipeline_writes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        first = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )

    pointer_before = (output / "current.json").read_bytes()
    detect = MagicMock(side_effect=AssertionError("detect must not run"))
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch("codeknow.pipeline.runner._cleanup_old_generations") as cleanup,
    ):
        second = run_pipeline(
            config,
            resolve_fn=lambda _config: root,
            detect_fn=detect,
        )

    assert second.generation_id == first.generation_id
    assert second.changed_paths == frozenset()
    assert (output / "current.json").read_bytes() == pointer_before
    detect.assert_not_called()
    cleanup.assert_not_called()


def test_failed_incremental_embedding_keeps_active_generation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("old\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="old"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["old"]}),
        )

    pointer_before = (output / "current.json").read_bytes()
    generations_before = set((output / "generations").iterdir())
    source.write_text("new\n", encoding="utf-8")

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="new"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch(
            "codeknow.pipeline.runner.diff_changes",
            return_value=[GitChange("M", "main.py")],
        ),
        pytest.raises(RuntimeError, match="embedding failed"),
    ):
        _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["new"]}),
            embed_fn=MagicMock(side_effect=RuntimeError("embedding failed")),
        )

    assert (output / "current.json").read_bytes() == pointer_before
    assert set((output / "generations").iterdir()) == generations_before


def test_failed_incremental_extraction_keeps_active_generation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("old\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="old"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["old"]}),
        )

    pointer_before = (output / "current.json").read_bytes()
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="new"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        pytest.raises(RuntimeError, match="extraction failed"),
    ):
        run_pipeline(
            config,
            resolve_fn=lambda _config: root,
            detect_fn=lambda _root: _discovery(source),
            extract_ast_fn=MagicMock(side_effect=RuntimeError("extraction failed")),
        )

    assert (output / "current.json").read_bytes() == pointer_before


def test_failed_generation_write_keeps_active_generation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("old\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="old"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["old"]}),
        )

    pointer_before = (output / "current.json").read_bytes()
    source.write_text("new\n", encoding="utf-8")
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="new"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch(
            "codeknow.pipeline.runner.diff_changes",
            return_value=[GitChange("M", "main.py")],
        ),
        patch(
            "codeknow.pipeline.runner.save_pipeline_result",
            side_effect=OSError("write failed"),
        ),
        pytest.raises(OSError, match="write failed"),
    ):
        _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["new"]}),
        )

    assert (output / "current.json").read_bytes() == pointer_before
    assert len(list((output / "generations").iterdir())) == 1


def test_missing_active_collection_forces_same_commit_rebuild(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
    )
    common_patches = (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner._cleanup_old_generations"),
    )
    with common_patches[0], common_patches[1], common_patches[2]:
        first = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )

    detect = MagicMock(return_value=_discovery(source))
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch(
            "codeknow.vector.chroma.validate_collection_records",
            return_value=False,
        ),
        patch("codeknow.pipeline.runner._cleanup_old_generations"),
    ):
        rebuilt = run_pipeline(
            config,
            resolve_fn=lambda _config: root,
            detect_fn=detect,
            extract_ast_fn=lambda _discovery: _extraction({"main.py": ["value"]}),
            build_graph_fn=_build,
            cluster_fn=lambda graph: {0: list(graph.nodes)},
            embed_fn=lambda result, **_kwargs: result,
        )

    assert rebuilt.generation_id != first.generation_id
    detect.assert_called_once()


def test_missing_old_commit_falls_back_to_full_build(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("old\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="old"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["old"]}),
        )

    source.write_text("new\n", encoding="utf-8")
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="new"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=False),
        patch(
            "codeknow.pipeline.runner.diff_changes",
            side_effect=AssertionError("diff must not run"),
        ) as diff,
    ):
        rebuilt = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["new"]}),
        )

    assert rebuilt.changed_paths is None
    diff.assert_not_called()


def test_custom_collection_base_is_published_with_generation_suffix(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=tmp_path / "graph",
        no_embed=True,
        chroma_collection="custom-base",
        graph_filename="custom-graph.json",
        chunk_map_filename="custom-chunks.json",
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="commit"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        result = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )

    current = load_current(config.resolved_output_dir())
    assert current is not None
    assert result.collection_name == current.collection_name
    assert current.collection_name.startswith("custom-base_")
    assert current.graph_filename == "custom-graph.json"
    assert current.chunk_map_filename == "custom-chunks.json"
    assert (current.directory / current.graph_filename).exists()
    assert (current.directory / current.chunk_map_filename).exists()


def test_wrong_active_vector_ids_with_same_count_force_full_rebuild(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner._cleanup_old_generations"),
    ):
        initial = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )
    current = load_current(output)
    assert current is not None
    expected_ids = {
        chunk.vector_id for chunks in initial.chunk_map.values() for chunk in chunks
    }
    metadata_path = current.directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["vector_count"] = len(expected_ids)
    metadata["vector_ids_digest"] = vector_ids_digest(expected_ids)
    metadata["vector_ids"] = sorted(expected_ids)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    detect = MagicMock(return_value=_discovery(source))

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch(
            "codeknow.vector.chroma.validate_collection_records",
            return_value=False,
        ),
        patch("codeknow.pipeline.runner._cleanup_old_generations"),
    ):
        rebuilt = run_pipeline(
            config,
            resolve_fn=lambda _config: root,
            detect_fn=detect,
            extract_ast_fn=lambda _discovery: _extraction({"main.py": ["value"]}),
            build_graph_fn=_build,
            cluster_fn=lambda graph: {0: list(graph.nodes)},
            embed_fn=lambda result, **_kwargs: result,
        )

    assert rebuilt.generation_id != initial.generation_id
    detect.assert_called_once()


def test_missing_vector_from_metadata_and_collection_forces_full_rebuild(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    first = root / "first.py"
    second = root / "second.py"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner._cleanup_old_generations"),
    ):
        initial = _run(
            config,
            root,
            _discovery(first, second),
            _extraction({"first.py": ["first"], "second.py": ["second"]}),
        )
    current = load_current(output)
    assert current is not None
    all_ids = sorted(
        chunk.vector_id for chunks in initial.chunk_map.values() for chunk in chunks
    )
    missing_snapshot = {all_ids[0]}
    metadata_path = current.directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["vector_ids"] = sorted(missing_snapshot)
    metadata["vector_count"] = len(missing_snapshot)
    metadata["vector_ids_digest"] = vector_ids_digest(missing_snapshot)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    detect = MagicMock(return_value=_discovery(first, second))

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch(
            "codeknow.vector.chroma.validate_collection_records",
            return_value=True,
        ),
        patch("codeknow.pipeline.runner._cleanup_old_generations"),
    ):
        rebuilt = run_pipeline(
            config,
            resolve_fn=lambda _config: root,
            detect_fn=detect,
            extract_ast_fn=lambda _discovery: _extraction(
                {"first.py": ["first"], "second.py": ["second"]}
            ),
            build_graph_fn=_build,
            cluster_fn=lambda graph: {0: list(graph.nodes)},
            embed_fn=lambda result, **_kwargs: result,
        )

    assert rebuilt.generation_id != initial.generation_id
    detect.assert_called_once()


def test_legacy_collection_is_deleted_only_after_successful_publish(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    output.mkdir()
    source = root / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    (output / "metadata.json").write_text(
        json.dumps({"commit_hash": None}),
        encoding="utf-8",
    )
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
    )
    legacy_name = "codeknow_owner-repo"

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="commit"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner._cleanup_old_generations"),
        patch("codeknow.vector.chroma.delete_collection") as delete,
        pytest.raises(RuntimeError, match="build failed"),
    ):
        _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
            embed_fn=MagicMock(side_effect=RuntimeError("build failed")),
        )

    assert legacy_name not in {
        call.args[0].collection_name for call in delete.call_args_list
    }

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="commit"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner._cleanup_old_generations"),
        patch("codeknow.vector.chroma.delete_collection") as delete,
    ):
        _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )

    assert legacy_name in {
        call.args[0].collection_name for call in delete.call_args_list
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [("commit_hash", None), ("schema_version", 1)],
)
def test_invalid_active_metadata_forces_full_rebuild(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        initial = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )
    current = load_current(output)
    assert current is not None
    metadata_path = current.directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata[field] = value
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        rebuilt = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )

    assert rebuilt.generation_id != initial.generation_id


def test_index_without_current_pointer_is_migrated_by_full_rebuild(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        initial = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )
    current = load_current(output)
    assert current is not None
    legacy_metadata = (current.directory / "metadata.json").read_text()
    (output / "current.json").unlink()
    (output / "metadata.json").write_text(legacy_metadata, encoding="utf-8")

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        rebuilt = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )

    assert rebuilt.generation_id != initial.generation_id
    assert load_current(output) is not None


def test_changed_embedding_model_forces_full_rebuild(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value\n", encoding="utf-8")
    initial_config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
        embed_model="model-a",
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        initial = _run(
            initial_config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )

    changed_config = PipelineConfig(
        repo_url=initial_config.repo_url,
        output_dir=output,
        no_embed=True,
        embed_model="model-b",
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        rebuilt = _run(
            changed_config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )

    assert rebuilt.generation_id != initial.generation_id


def test_changed_extraction_version_forces_full_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        initial = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )

    monkeypatch.setattr("codeknow.pipeline.config.EXTRACTION_CACHE_VERSION", 999)
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        rebuilt = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )

    assert rebuilt.generation_id != initial.generation_id
