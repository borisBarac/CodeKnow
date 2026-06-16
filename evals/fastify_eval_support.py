from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from codeknow.vector.chroma import ChromaConfig, ChromaStore
from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings

logger = logging.getLogger(__name__)

EVALS_DIR = Path(__file__).parent
ENV_FILE = EVALS_DIR / ".env"
REPO_DIR = EVALS_DIR / "fastify-main"
GRAPH_DIR = EVALS_DIR / ".cache" / "fastify-graph"
CHROMA_COLLECTION = "codeknow_fastify_eval"
MIN_EXPECTED_CHUNKS = 200
REQUIRED_INDEX_FILES = [
    "lib/route.js",
    "lib/handle-request.js",
    "lib/plugin-override.js",
    "lib/schema-controller.js",
    "lib/content-type-parser.js",
]
BUILD_COMMAND = "uv run python evals/build_fastify_graph.py"


def _load_eval_env() -> None:
    """Load eval-local env defaults without overriding the shell environment."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _make_store() -> ChromaStore:
    embeddings = create_embeddings(EmbeddingConfig())
    config = ChromaConfig(collection_name=CHROMA_COLLECTION)
    return ChromaStore(config=config, embeddings=embeddings)


def _index_is_healthy() -> bool:  # noqa: PLR0911
    """Check whether the cached graph + chunk map + Chroma are usable."""
    graph_file = GRAPH_DIR / "graph.json"
    chunk_map_file = GRAPH_DIR / "chunk_map.json"
    if not graph_file.exists():
        logger.info("index unhealthy: graph.json missing")
        return False
    if not chunk_map_file.exists():
        logger.info("index unhealthy: chunk_map.json missing")
        return False

    raw = json.loads(chunk_map_file.read_text(encoding="utf-8"))
    chunk_count = sum(len(chunks) for chunks in raw.values())
    if chunk_count < MIN_EXPECTED_CHUNKS:
        logger.info(
            "index unhealthy: %d chunks < MIN_EXPECTED_CHUNKS=%d",
            chunk_count,
            MIN_EXPECTED_CHUNKS,
        )
        return False

    indexed_files = set(raw.keys())
    for required in REQUIRED_INDEX_FILES:
        found = any(f.endswith(required) or required in f for f in indexed_files)
        if not found:
            logger.info("index unhealthy: %s not in chunk_map.json", required)
            return False

    try:
        store = _make_store()
        chroma_count = store.count()
    except Exception as exc:
        logger.info("index unhealthy: cannot query Chroma: %s", exc)
        return False

    if chroma_count < MIN_EXPECTED_CHUNKS:
        logger.info(
            "index unhealthy: Chroma has %d chunks < %d",
            chroma_count,
            MIN_EXPECTED_CHUNKS,
        )
        return False

    logger.info("index healthy: %d chunks, %d in Chroma", chunk_count, chroma_count)
    return True


def _assert_eval_index_ready() -> None:
    """Fail fast before LLM calls if the index is missing expected files."""
    if _env_flag("SKIP_INDEX_SANITY"):
        logger.info("SKIP_INDEX_SANITY=1: skipping index sanity check")
        return

    chunk_map_file = GRAPH_DIR / "chunk_map.json"
    if not chunk_map_file.exists():
        msg = (
            "Fastify eval index is missing chunk_map.json. "
            f"Build it first with `{BUILD_COMMAND}`."
        )
        raise RuntimeError(msg)

    raw = json.loads(chunk_map_file.read_text(encoding="utf-8"))
    indexed_files = set(raw.keys())

    missing: list[str] = []
    for required in REQUIRED_INDEX_FILES:
        if not any(f.endswith(required) or required in f for f in indexed_files):
            missing.append(required)

    has_js = any(f.endswith(".js") for f in indexed_files)
    has_ts = any(f.endswith(".ts") for f in indexed_files)

    if missing:
        msg = (
            f"Index sanity check failed: missing {len(missing)} expected file(s) "
            f"in chunk_map.json: {missing}. Rebuild with `{BUILD_COMMAND}`."
        )
        raise RuntimeError(msg)
    if not has_js:
        msg = (
            "Index sanity check failed: no .js files in chunk_map.json. "
            f"Rebuild with `{BUILD_COMMAND}`."
        )
        raise RuntimeError(msg)
    if not has_ts:
        msg = (
            "Index sanity check failed: no .ts files in chunk_map.json. "
            f"Rebuild with `{BUILD_COMMAND}`."
        )
        raise RuntimeError(msg)

    logger.info("Index sanity check passed (%d indexed files)", len(indexed_files))


def assert_prebuilt_index_ready() -> None:
    if not _index_is_healthy():
        msg = (
            "Fastify eval index is missing or unhealthy. "
            f"Build it first with `{BUILD_COMMAND}`."
        )
        raise RuntimeError(msg)
    _assert_eval_index_ready()


def reset_graph_dir() -> None:
    if GRAPH_DIR.exists():
        logger.info("Resetting graph cache: %s", GRAPH_DIR)
        shutil.rmtree(GRAPH_DIR)


def reset_chroma_collection() -> None:
    logger.info("Dropping Chroma collection: %s", CHROMA_COLLECTION)
    _make_store().drop_collection()
