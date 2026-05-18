"""E2E tests for embedding generation and vector search.

Verifies the full lifecycle: generate embeddings from real code text,
store them in ChromaDB, search by text and by vector, and delete.

No pytest fixtures — all setup runs at module import time:
  1. Health-check Ollama & ChromaDB via check_services.py
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

import chromadb
from check_services import check_chroma, check_ollama
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
check_ollama()
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
