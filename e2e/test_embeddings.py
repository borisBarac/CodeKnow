"""E2E tests for embedding generation and vector search.

Verifies the full lifecycle: generate embeddings from real code text,
store them in ChromaDB, search by text and by vector, and delete.

No pytest fixtures — all setup runs at module import time:
  1. Health-check Docker Model Runner & ChromaDB via check_services.py
  2. Create Embeddings + ChromaStore
  3. Write real Python source files to a temp dir
  4. Build Chunk objects and store embeddings
  5. atexit cleanup deletes the test collection

Env vars are loaded by e2e/conftest.py.
"""

from __future__ import annotations

import atexit
import hashlib
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

import chromadb
from check_services import check_chroma, check_docker_model_runner
from codeknow.schemas import Chunk
from codeknow.vector import (
    ChromaConfig,
    ChromaStore,
    EmbeddingConfig,
    create_embeddings,
    embed_texts,
)

logger = logging.getLogger(__name__)

_SLUG = "e2e-embeddings-test"

_AUTH_CODE = (
    "def authenticate(user, password):\n"
    "    if user == 'admin' and password == 'secret':\n"
    "        return True\n"
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


# ── 1. Health-check services ─────────────────────────────────────────
check_docker_model_runner()
check_chroma()

# ── 2. Create clients ────────────────────────────────────────────────
_emb_cfg = EmbeddingConfig()
EMBEDDINGS = create_embeddings(_emb_cfg)
logger.info(
    "Created embeddings: provider=%s model=%s", _emb_cfg.provider, _emb_cfg.model
)

_COLLECTION = f"e2e_test_emb_{int(time.time())}"
_store_config = ChromaConfig(collection_name=_COLLECTION)
STORE = ChromaStore(config=_store_config, embeddings=EMBEDDINGS)
logger.info("Created ChromaStore: collection=%s", _COLLECTION)

# ── 3. Write real source files ───────────────────────────────────────
_TMP = tempfile.mkdtemp(prefix="e2e_emb_")
TMP_DIR = Path(_TMP)

(TMP_DIR / "auth.py").write_text(_AUTH_CODE)
(TMP_DIR / "math_utils.py").write_text(_MATH_CODE)
(TMP_DIR / "database.py").write_text(_DB_CODE)

# ── 4. Build chunks and store ────────────────────────────────────────
_files_code = {
    "auth.py": _AUTH_CODE,
    "math_utils.py": _MATH_CODE,
    "database.py": _DB_CODE,
}

CHUNKS: list[Chunk] = []
for _fname, _code in _files_code.items():
    CHUNKS.append(
        Chunk(
            file=str(TMP_DIR / _fname),
            start_line=1,
            end_line=max(_code.count("\n"), 1),
            hash=_sha(_code),
        )
    )

STORE.store_chunks(CHUNKS, slug=_SLUG)
logger.info("Stored %d chunks", len(CHUNKS))

# ── 5. Cleanup ───────────────────────────────────────────────────────


def _cleanup():
    try:
        client = chromadb.HttpClient(
            host=_store_config.resolved_host(),
            port=_store_config.resolved_port(),
        )
        client.delete_collection(_COLLECTION)
        logger.info("Deleted test collection: %s", _COLLECTION)
    except Exception:
        logger.warning("Could not delete collection: %s", _COLLECTION)


atexit.register(_cleanup)

# ── Tests ─────────────────────────────────────────────────────────────


def test_embed_texts_returns_vectors():
    vectors = embed_texts(["hello world", "authentication"], EMBEDDINGS)
    assert isinstance(vectors, list)
    assert len(vectors) == 2
    for vec in vectors:
        assert isinstance(vec, list)
        assert len(vec) > 0
        assert all(isinstance(v, float) for v in vec)


def test_store_chunks_persists():
    count = STORE.count()
    logger.info("Collection count: %d", count)
    assert count == len(CHUNKS)


def test_search_by_text():
    results = STORE.search("user authentication", n_results=3)
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


def test_search_by_vector():
    vec = embed_texts(["connect to database"], EMBEDDINGS)[0]
    results = STORE.search(vec, n_results=3)
    logger.info("Vector search returned %d results", len(results))
    assert len(results) > 0

    for r in results:
        assert r.hash
        assert r.distance is not None
        logger.info("  hash=%s… dist=%.4f", r.hash[:12], r.distance)


def test_search_ranks_relevant_first():
    results = STORE.search("authenticate user login", n_results=len(CHUNKS))
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


def test_delete_removes_chunk():
    content = "def isolated_function():\n    pass\n"
    h = _sha(content)
    (TMP_DIR / "isolated.py").write_text(content)

    chunk = Chunk(
        file=str(TMP_DIR / "isolated.py"),
        start_line=1,
        end_line=2,
        hash=h,
    )
    STORE.store_chunks([chunk], slug=_SLUG)
    before = STORE.count()
    logger.info("Count before delete: %d", before)

    STORE.delete([h])
    after = STORE.count()
    logger.info("Count after delete: %d", after)
    assert after == before - 1


def _make_synthetic_chunk(idx: int) -> Chunk:
    content = (
        f"def handler_{idx}(value):\n"
        "    total = 0\n"
        + "".join(f"    total += {i} * value\n" for i in range(50))
        + "    return total\n"
    )
    path = TMP_DIR / f"synthetic_handler_{idx}.py"
    path.write_text(content)
    return Chunk(
        file=str(path),
        start_line=1,
        end_line=content.count("\n") + 1,
        hash=_sha(content),
    )


def test_store_chunks_splits_embedding_requests():
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

    collection = f"e2e_test_batch_{int(time.time())}"
    store_config = ChromaConfig(collection_name=collection)
    store = ChromaStore(config=store_config, embeddings=embeddings)

    chunks = [_make_synthetic_chunk(i) for i in range(num_chunks)]

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
        store.store_chunks(chunks, slug=_SLUG, batch_size=num_chunks)
        stored_count = store.count()
    finally:
        embeddings.client.create = original_create
        try:
            client = chromadb.HttpClient(
                host=store_config.resolved_host(),
                port=store_config.resolved_port(),
            )
            client.delete_collection(collection)
            logger.info("Deleted batch test collection: %s", collection)
        except Exception:
            logger.warning("Could not delete collection: %s", collection)

    logger.info("Embedding request sizes: %s", request_sizes)

    assert stored_count == num_chunks
    assert sum(request_sizes) == num_chunks
    assert max(request_sizes) <= request_chunk_size
    assert len(request_sizes) == num_chunks // request_chunk_size
