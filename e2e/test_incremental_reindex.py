"""Opt-in QA for incremental reindexing against the Docker stack."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
import httpx
import pytest
from git import Repo

pytestmark = pytest.mark.skipif(
    os.getenv("CODEKNOW_INCREMENTAL_E2E") != "1",
    reason="set CODEKNOW_INCREMENTAL_E2E=1",
)

SSH_URL = "git@github.com:eMahtab/node-express-hello-world.git"
HTTPS_URL = "https://github.com/eMahtab/node-express-hello-world.git"
SLUG = "eMahtab-node-express-hello-world"
API_URL = "http://localhost:8080"
MIRROR_CONTAINER = "/data/qa-incremental-mirror.git"
FIXTURES = {
    "qa/modified.js": "export function modified() { return 'before'; }\n",
    "qa/copied.js": "export function copied() { return 'stable'; }\n",
    "qa/renamed.js": "export function renamed() { return 'rename'; }\n",
    "qa/deleted.js": "export function deleted() { return 'delete'; }\n",
}


@dataclass(frozen=True)
class Snapshot:
    pointer: dict[str, Any]
    metadata: dict[str, Any]
    graph: dict[str, Any]
    chunks: dict[str, list[dict[str, Any]]]
    records: dict[str, dict[str, Any]]


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        args,
        check=check,
        text=True,
        capture_output=True,
    )


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run("docker", *args, check=check)


def _cli(*args: str) -> str:
    result = _run(sys.executable, "-m", "codeknow_cli.main", *args)
    assert "Status: succeeded" in result.stdout
    return result.stdout


def _write_fixtures(root: Path) -> None:
    for relative, content in FIXTURES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _commit(repo: Repo, message: str) -> str:
    repo.git.add("-A")
    return repo.index.commit(message).hexsha


def _collection_records(name: str) -> dict[str, dict[str, Any]]:
    client = chromadb.HttpClient(host="localhost", port=8018)
    raw = client.get_collection(name).get(include=["documents", "metadatas"])
    ids = raw.get("ids", []) or []
    documents = raw.get("documents", []) or []
    metadatas = raw.get("metadatas", []) or []
    return {
        vector_id: {"document": documents[index], "metadata": metadatas[index]}
        for index, vector_id in enumerate(ids)
    }


def _snapshot() -> Snapshot:
    slug_dir = Path("infra/api-data/graph") / SLUG
    pointer = json.loads((slug_dir / "current.json").read_text(encoding="utf-8"))
    generation = slug_dir / "generations" / pointer["generation_id"]
    metadata = json.loads((generation / "metadata.json").read_text(encoding="utf-8"))
    graph = json.loads(
        (generation / pointer.get("graph_filename", "graph.json")).read_text(
            encoding="utf-8"
        )
    )
    chunks = json.loads(
        (generation / pointer.get("chunk_map_filename", "chunk_map.json")).read_text(
            encoding="utf-8"
        )
    )
    return Snapshot(
        pointer=pointer,
        metadata=metadata,
        graph=graph,
        chunks=chunks,
        records=_collection_records(pointer["collection_name"]),
    )


def _vectors(snapshot: Snapshot, path: str) -> set[str]:
    return {
        chunk["vector_id"]
        for chunk in snapshot.chunks.get(path, [])
        if chunk["embeddable"]
    }


def _canonical_graph(graph: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    def encoded(item: dict[str, Any]) -> str:
        return json.dumps(item, sort_keys=True, separators=(",", ":"))

    nodes = sorted(graph.get("nodes", []), key=encoded)
    edges = sorted(graph.get("links", graph.get("edges", [])), key=encoded)
    return {"nodes": nodes, "edges": edges}


def _assert_integrity(snapshot: Snapshot, commit: str) -> None:
    assert snapshot.metadata["commit_hash"] == commit
    assert snapshot.metadata["generation_id"] == snapshot.pointer["generation_id"]
    assert snapshot.metadata["collection_name"] == snapshot.pointer["collection_name"]
    chunk_ids = {
        chunk["vector_id"] for chunks in snapshot.chunks.values() for chunk in chunks
    }
    embeddable_ids = {
        chunk["vector_id"]
        for chunks in snapshot.chunks.values()
        for chunk in chunks
        if chunk["embeddable"]
    }
    graph_ids = {
        ref["vector_id"]
        for node in snapshot.graph.get("nodes", [])
        for ref in node.get("chunks", [])
    }
    assert graph_ids <= chunk_ids
    assert set(snapshot.records) == embeddable_ids
    assert len(snapshot.records) == len(set(snapshot.records))
    assert all(
        record["metadata"]["slug"] == SLUG for record in snapshot.records.values()
    )


@pytest.fixture
def indexed_repo(tmp_path: Path):
    assert httpx.get(f"{API_URL}/health", timeout=5).status_code == 200
    assert httpx.get("http://localhost:8018/api/v2/heartbeat", timeout=5).is_success
    assert _docker("exec", "redis", "redis-cli", "ping").stdout.strip() == "PONG"
    assert httpx.get("http://localhost:12434/engines/v1/models", timeout=10).is_success

    repos = httpx.get(f"{API_URL}/v1/repos", timeout=5).json()["repos"]
    assert SLUG not in {repo["slug"] for repo in repos}, f"{SLUG} already indexed"

    source = tmp_path / "source"
    Repo.clone_from(HTTPS_URL, source)
    _write_fixtures(source)
    fixture_commit = _commit(Repo(source), "add incremental QA fixtures")
    mirror = Path("infra/api-data/qa-incremental-mirror.git").resolve()
    assert not mirror.exists(), f"stale mirror exists: {mirror}"
    Repo.clone_from(source, mirror, mirror=True)
    rewrite_key = f"url.file://{MIRROR_CONTAINER}.insteadOf"
    _docker("exec", "codeknow-api", "git", "config", "--global", rewrite_key, SSH_URL)

    try:
        output = _cli("add", SSH_URL)
        assert f"Commit: {fixture_commit}" in output
        yield Path("infra/api-data/temp") / SLUG, fixture_commit
    finally:
        _run(sys.executable, "-m", "codeknow_cli.main", "remove", SLUG, check=False)
        _docker(
            "exec",
            "codeknow-api",
            "git",
            "config",
            "--global",
            "--unset-all",
            rewrite_key,
            check=False,
        )
        shutil.rmtree(mirror, ignore_errors=True)

        repos = httpx.get(f"{API_URL}/v1/repos", timeout=5).json()["repos"]
        assert SLUG not in {repo["slug"] for repo in repos}
        assert (
            SLUG not in _run(sys.executable, "-m", "codeknow_cli.main", "info").stdout
        )
        assert not (Path("infra/api-data/graph") / SLUG).exists()
        assert not (Path("infra/api-data/temp") / SLUG).exists()
        client = chromadb.HttpClient(host="localhost", port=8018)
        names = {
            item if isinstance(item, str) else item.name
            for item in client.list_collections()
        }
        assert not any(SLUG in name for name in names)
        assert all(
            not client.get_collection(name).get(where={"slug": SLUG})["ids"]
            for name in names
        )


def test_incremental_reindex_matches_full_rebuild(indexed_repo) -> None:
    checkout, initial_commit = indexed_repo
    initial = _snapshot()
    _assert_integrity(initial, initial_commit)

    (checkout / "qa/modified.js").write_text(
        "export function modified() { return 'after'; }\n", encoding="utf-8"
    )
    shutil.copy2(checkout / "qa/copied.js", checkout / "qa/copied-new.js")
    (checkout / "qa/renamed.js").rename(checkout / "qa/renamed-new.js")
    (checkout / "qa/deleted.js").unlink()
    updated_commit = _commit(Repo(checkout), "exercise incremental QA changes")

    output = _cli("reindex", SLUG, "--no-fetch")
    assert f"Commit: {updated_commit}" in output
    incremental = _snapshot()
    _assert_integrity(incremental, updated_commit)

    assert incremental.pointer["generation_id"] != initial.pointer["generation_id"]
    assert incremental.pointer["collection_name"] != initial.pointer["collection_name"]
    assert _canonical_graph(initial.graph) != _canonical_graph(incremental.graph)
    assert _vectors(initial, "qa/modified.js").isdisjoint(incremental.records)
    assert _vectors(initial, "qa/modified.js") != _vectors(
        incremental, "qa/modified.js"
    )
    assert _vectors(incremental, "qa/copied-new.js")
    assert _vectors(initial, "qa/copied.js") == _vectors(incremental, "qa/copied.js")
    assert "qa/renamed.js" not in incremental.chunks
    assert _vectors(incremental, "qa/renamed-new.js")
    assert "qa/deleted.js" not in incremental.chunks
    assert "qa/deleted.js" not in json.dumps(incremental.graph, sort_keys=True)
    assert not _vectors(initial, "qa/deleted.js") & incremental.records.keys()

    output = _cli("rebuild", SLUG, "--no-fetch")
    assert f"Commit: {updated_commit}" in output
    rebuilt = _snapshot()
    _assert_integrity(rebuilt, updated_commit)

    assert rebuilt.pointer["generation_id"] != incremental.pointer["generation_id"]
    assert _canonical_graph(incremental.graph) == _canonical_graph(rebuilt.graph)
    assert incremental.chunks == rebuilt.chunks
    assert incremental.records == rebuilt.records
    for key in (
        "node_count",
        "edge_count",
        "community_count",
        "vector_count",
    ):
        assert incremental.metadata[key] == rebuilt.metadata[key]
    assert len(incremental.chunks) == len(rebuilt.chunks)
