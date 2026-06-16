from __future__ import annotations

import logging
import os

from codeknow.pipeline import PipelineConfig, run_pipeline
from codeknow.service_checks import (
    check_chroma,
    check_docker_model_runner,
    check_ollama,
)
from fastify_eval_support import (
    CHROMA_COLLECTION,
    GRAPH_DIR,
    REPO_DIR,
    _env_flag,
    _index_is_healthy,
    _load_eval_env,
    reset_chroma_collection,
    reset_graph_dir,
)

logger = logging.getLogger(__name__)

REPO_URL = "https://github.com/fastify/fastify"


def _preflight_external_services() -> None:
    """Fail before deleting/building anything if required services are down."""
    check_chroma()

    provider = os.environ.get("EMBEDDING_PROVIDER", "docker").strip().lower()
    if provider == "docker":
        check_docker_model_runner()
    elif provider == "ollama":
        check_ollama()
    elif provider != "openrouter":
        msg = f"Unsupported EMBEDDING_PROVIDER={provider!r}"
        raise ValueError(msg)


def _rebuild_index() -> None:
    _preflight_external_services()
    reset_graph_dir()
    reset_chroma_collection()

    logger.info(
        "Indexing %s -> %s via codeknow.pipeline.run_pipeline",
        REPO_DIR,
        GRAPH_DIR,
    )
    config = PipelineConfig(
        repo_url=REPO_URL,
        output_dir=GRAPH_DIR,
        chroma_collection=CHROMA_COLLECTION,
    )
    run_pipeline(
        config,
        resolve_fn=lambda _config: REPO_DIR,
        progress_callback=lambda stage, percent, message: logger.info(
            "[%d%%] %s: %s", percent, stage, message
        ),
    )


def main() -> None:
    _load_eval_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if _env_flag("FORCE_REINDEX"):
        logger.info("FORCE_REINDEX=1: rebuilding graph + embeddings")
        _rebuild_index()
        return

    if _index_is_healthy():
        logger.info("Index healthy: skipping rebuild")
        return

    logger.info("Index unhealthy or missing: rebuilding")
    _rebuild_index()


if __name__ == "__main__":
    main()
