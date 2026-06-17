"""E2E tests for embedding generation and vector search.

Verifies the full lifecycle: generate embeddings from real code text,
store them in ChromaDB, search by text and by vector, and delete.

Setup runs lazily via the module-scoped ``embed_env`` fixture (service
health-checks, client creation, real source files, chunks, store) and the
Chroma collection is dropped in fixture teardown.

Env vars are loaded by e2e/conftest.py.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import uuid4

import chromadb
import pytest
from check_services import check_chroma, check_docker_model_runner, check_ollama
from codeknow.schemas import Chunk
from codeknow.vector import (
    ChromaConfig,
    ChromaStore,
    EmbeddingConfig,
    create_embeddings,
    embed_texts,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

_SLUG = "e2e-embeddings-test"

_AUTH_CODE = (
    "def authenticate(user, password):\n"
    "    if user == 'admin' and password == 'secret':\n"
    "    return True\n"
    "    return False\n"
)
_MATH_CODE = (
    "def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n"
)
_DB_CODE = (
    "import sqlite3\n\n"
    "def connect(db_path):\n"
    "    return sqlite3.connect(db_path)\n\n"
    "def query(conn, sql):\n"
    "    cursor = conn.cursor()\n"
    "    cursor.execute(sql)\n"
    "    return cursor.fetchall()\n"
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _file(r) -> str:
    return r.metadata.get("file", "") if r.metadata else ""


def _check_embedding_services() -> None:
    """Health-check the embedding backend + Chroma, branching on EMBEDDING_PROVIDER.

    Mirrors e2e/check_services.py: Docker Model Runner for ``docker``,
    Ollama for ``ollama``; the ``openrouter`` provider has no local service
    to ping. Previously this ran ``check_docker_model_runner()`` unconditionally
    at import time, which broke collection under non-docker providers.
    """
    provider = os.environ.get("EMBEDDING_PROVIDER", "docker").strip().lower()
    if provider == "docker":
        check_docker_model_runner()
    elif provider == "ollama":
        check_ollama()
    check_chroma()


def _delete_collection(store_config: ChromaConfig, collection: str) -> None:
    try:
        client = chromadb.HttpClient(
            host=store_config.resolved_host(),
            port=store_config.resolved_port(),
        )
        client.delete_collection(collection)
        logger.info("Deleted test collection: %s", collection)
    except Exception:
        logger.warning("Could not delete collection: %s", collection, exc_info=True)


class EmbedEnv(NamedTuple):
    embeddings: Embeddings
    store: ChromaStore
    chunks: list[Chunk]
    tmp_dir: Path
    slug: str
    collection: str


@pytest.fixture(scope="module")
def embed_env() -> Iterator[EmbedEnv]:
    # 1. Health-check services.
    _check_embedding_services()

    # 2. Create clients.
    emb_cfg = EmbeddingConfig()
    embeddings = create_embeddings(emb_cfg)
    logger.info(
        "Created embeddings: provider=%s model=%s", emb_cfg.provider, emb_cfg.model
    )
    collection = f"e2e_test_emb_{uuid4().hex[:8]}"
    store_config = ChromaConfig(collection_name=collection)
    store = ChromaStore(config=store_config, embeddings=embeddings)
    logger.info("Created ChromaStore: collection=%s", collection)

    # 3. Write real source files.
    tmp_dir = Path(tempfile.mkdtemp(prefix="e2e_emb_"))
    (tmp_dir / "auth.py").write_text(_AUTH_CODE)
    (tmp_dir / "math_utils.py").write_text(_MATH_CODE)
    (tmp_dir / "database.py").write_text(_DB_CODE)

    # 4. Build chunks and store.
    files_code = {
        "auth.py": _AUTH_CODE,
        "math_utils.py": _MATH_CODE,
        "database.py": _DB_CODE,
    }
    chunks = [
        Chunk(
            file=str(tmp_dir / fname),
            start_line=1,
            end_line=max(code.count("\n"), 1),
            hash=_sha(code),
        )
        for fname, code in files_code.items()
    ]
    store.store_chunks(chunks, slug=_SLUG)
    logger.info("Stored %d chunks", len(chunks))

    yield EmbedEnv(embeddings, store, chunks, tmp_dir, _SLUG, collection)

    # 5. Teardown — drop the Chroma collection (yield-based, not atexit).
    _delete_collection(store_config, collection)


# ── Tests ─────────────────────────────────────────────────────────────


def test_embed_texts_returns_vectors(embed_env: EmbedEnv) -> None:
    vectors = embed_texts(["hello world", "authentication"], embed_env.embeddings)
    assert isinstance(vectors, list)
    assert len(vectors) == 2
    for vec in vectors:
        assert isinstance(vec, list)
        assert len(vec) > 0
        assert all(isinstance(v, float) for v in vec)


def test_store_chunks_persists(embed_env: EmbedEnv) -> None:
    count = embed_env.store.count()
    logger.info("Collection count: %d", count)
    assert count == len(embed_env.chunks)


def test_search_by_text(embed_env: EmbedEnv) -> None:
    results = embed_env.store.search("user authentication", n_results=3)
    logger.info("Text search returned %d results", len(results))
    assert len(results) > 0

    for r in results:
        assert r.hash
        assert r.distance is not None
        assert r.metadata is not None
        assert "file" in r.metadata
        logger.info(
            "  hash=%s… dist=%.4f file=%s",
            r.hash[:12],
            r.distance,
            _file(r),
        )


def test_search_by_vector(embed_env: EmbedEnv) -> None:
    vecs = embed_texts(
        ["connect to database", "user authentication"], embed_env.embeddings
    )
    assert len(vecs) == 2
    results = embed_env.store.search(vecs[0], n_results=3)
    logger.info("Vector search returned %d results", len(results))
    assert len(results) > 0

    for r in results:
        assert r.hash
        assert r.distance is not None
        logger.info("  hash=%s… dist=%.4f", r.hash[:12], r.distance)


def test_search_ranks_relevant_first(embed_env: EmbedEnv) -> None:
    results = embed_env.store.search(
        "authenticate user login", n_results=len(embed_env.chunks)
    )
    logger.info("Ranking results:")
    for r in results:
        logger.info("  dist=%.4f file=%s", r.distance, _file(r) or "?")

    auth = [r for r in results if "auth" in _file(r)]
    other = [r for r in results if "auth" not in _file(r)]

    assert auth, "expected at least one auth.py result"
    assert other, "expected at least one non-auth result"

    best_auth = min(r.distance for r in auth)
    best_other = min(r.distance for r in other)
    logger.info("best auth dist=%.4f vs best other dist=%.4f", best_auth, best_other)
    assert best_auth < best_other


def test_delete_removes_chunk(embed_env: EmbedEnv) -> None:
    content = "def isolated_function():\n    pass\n"
    h = _sha(content)
    (embed_env.tmp_dir / "isolated.py").write_text(content)

    chunk = Chunk(
        file=str(embed_env.tmp_dir / "isolated.py"),
        start_line=1,
        end_line=2,
        hash=h,
    )
    embed_env.store.store_chunks([chunk], slug=embed_env.slug)
    before = embed_env.store.count()
    logger.info("Count before delete: %d", before)

    embed_env.store.delete([h])
    after = embed_env.store.count()
    logger.info("Count after delete: %d", after)
    assert after == before - 1


def _make_synthetic_chunk(tmp_dir: Path, idx: int) -> Chunk:
    content = (
        f"def handler_{idx}(value):\n"
        "    total = 0\n"
        + "".join(f"    total += {i} * value\n" for i in range(50))
        + "    return total\n"
    )
    path = tmp_dir / f"synthetic_handler_{idx}.py"
    path.write_text(content)
    return Chunk(
        file=str(path),
        start_line=1,
        end_line=content.count("\n") + 1,
        hash=_sha(content),
    )


def test_store_chunks_splits_embedding_requests(embed_env: EmbedEnv) -> None:
    """Regression: store_chunks must split texts into small /embeddings requests.

    With check_embedding_ctx_length=False (local providers), OpenAIEmbeddings
    sends the full texts list in one POST unless chunk_size is set.  A large
    store batch therefore produces an oversized request that local servers
    reject with "Context size has been exceeded".

    This test instruments the real embedding client to verify that the request
    is actually split according to request_chunk_size.
    """
    request_chunk_size = 4
    num_chunks = 12

    emb_cfg = EmbeddingConfig(request_chunk_size=request_chunk_size)
    embeddings = create_embeddings(emb_cfg)

    # Force lazy client initialisation so we can wrap its create() method.
    embeddings.embed_query("warmup")

    collection = f"e2e_test_batch_{uuid4().hex[:8]}"
    store_config = ChromaConfig(collection_name=collection)
    store = ChromaStore(config=store_config, embeddings=embeddings)

    chunks = [_make_synthetic_chunk(embed_env.tmp_dir, i) for i in range(num_chunks)]

    request_sizes: list[int] = []
    original_create = embeddings.client.create

    def recording_create(*args: Any, **kwargs: Any):
        inp = kwargs.get("input")
        if inp is None and args:
            inp = args[0]
        if isinstance(inp, list):
            request_sizes.append(len(inp))
        return original_create(*args, **kwargs)

    stored_count = 0
    try:
        embeddings.client.create = recording_create
        store.store_chunks(chunks, slug=embed_env.slug, batch_size=num_chunks)
        stored_count = store.count()
    finally:
        embeddings.client.create = original_create
        _delete_collection(store_config, collection)

    logger.info("Embedding request sizes: %s", request_sizes)

    assert stored_count == num_chunks
    assert sum(request_sizes) == num_chunks
    assert max(request_sizes) <= request_chunk_size
    assert len(request_sizes) == num_chunks // request_chunk_size
