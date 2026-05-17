"""FastAPI application factory and entry-point for the codeknow API service."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query

from codeknow_api.middleware import StubMiddleware

GRAPH_DIR = Path(os.getenv("CODEKNOW_GRAPH_DIR", "./graph"))
TEMP_DIR = Path(os.getenv("CODEKNOW_TEMP_DIR", "./temp"))

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="CodeKnow API",
        version="0.1.0",
        description="Knowledge graph service for code",
    )
    app.add_middleware(StubMiddleware)
    app.state.build_status = {}  # type: ignore[assignment]

    @app.post("/v1/build", status_code=202)
    async def build(body: dict[str, Any]) -> dict[str, Any]:
        from codeknow.pipeline import PipelineConfig, run_pipeline

        github_ssh_url = body.get("github_ssh_url", "")
        slug = PipelineConfig(repo_url=github_ssh_url).slug
        config = PipelineConfig(
            repo_url=github_ssh_url,
            input_dir=TEMP_DIR,
            output_dir=GRAPH_DIR / slug,
        )

        app.state.build_status[slug] = {"status": "building", "progress": 0}
        try:
            result = await asyncio.to_thread(run_pipeline, config)
        except Exception as exc:
            app.state.build_status[slug] = {"status": "error", "progress": 0}
            return {"status": "error", "error": str(exc)}
        shutil.rmtree(TEMP_DIR / slug, ignore_errors=True)
        app.state.build_status[slug] = {"status": "done", "progress": 100}
        return {
            "status": "done",
            "slug": slug,
            "commit_hash": result.commit_hash,
        }

    @app.post("/v1/search")
    async def search(body: dict[str, Any]) -> dict[str, Any]:
        from codeknow.vector.multi_search import multi_graph_search

        query = body.get("query", "")
        top_k = body.get("top_k", 10)
        repos = body.get("repos")
        if repos is not None and not isinstance(repos, list):
            return {"error": "repos must be a list of slugs", "results": []}
        result = multi_graph_search(
            query,
            graph_base_dir=GRAPH_DIR,
            slugs=repos,
            total_limit=top_k,
        )
        return result.model_dump()

    @app.delete("/v1/repos")
    async def delete_repo(url: Annotated[str, Query()]) -> dict[str, Any]:
        from codeknow.git_download import unregister
        from codeknow.pipeline import PipelineConfig
        from codeknow.vector.chroma import ChromaConfig, ChromaStore
        from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings

        config = PipelineConfig(repo_url=url)
        slug = config.slug

        shutil.rmtree(GRAPH_DIR / slug, ignore_errors=True)
        shutil.rmtree(TEMP_DIR / slug, ignore_errors=True)

        try:
            embeddings = create_embeddings(EmbeddingConfig())
            collection_name = f"codeknow_{slug}"
            store = ChromaStore(
                config=ChromaConfig(collection_name=collection_name),
                embeddings=embeddings,
            )
            store.delete_by_slug(slug)
        except Exception:
            logger.warning(
                "ChromaDB deletion failed for slug '%s'", slug, exc_info=True
            )

        unregister(url)
        return {"status": "deleted", "slug": slug}

    @app.get("/v1/repos")
    async def list_repos() -> dict[str, Any]:
        from codeknow.pipeline import load_metadata

        repos: list[dict[str, Any]] = []
        if GRAPH_DIR.is_dir():
            for child in sorted(GRAPH_DIR.iterdir()):
                if child.is_dir():
                    meta = load_metadata(child)
                    if meta is not None:
                        repos.append(meta)
        return {"repos": repos}

    return app


def main() -> None:
    """Run the API server."""
    import uvicorn

    host = os.getenv("CODEKNOW_API_HOST", "127.0.0.1")
    uvicorn.run(
        "codeknow_api.app:create_app",
        factory=True,
        host=host,
        port=8080,
    )
