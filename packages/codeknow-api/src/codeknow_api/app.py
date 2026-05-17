"""FastAPI application factory and entry-point for the codeknow API service."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from codeknow.schemas import ListReposResponse, RepoMetadata
from fastapi import FastAPI, HTTPException, Query

from codeknow_api.middleware import StubMiddleware
from codeknow_api.models import BuildRequest, BuildResponse, DeleteRepoRequest

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
    app.state.build_locks: dict[str, asyncio.Lock] = {}

    @app.post("/v1/build", status_code=202)
    async def build(body: BuildRequest) -> BuildResponse:
        from codeknow.pipeline import PipelineConfig, run_pipeline

        slug = PipelineConfig(repo_url=body.github_ssh_url).slug
        config = PipelineConfig(
            repo_url=body.github_ssh_url,
            input_dir=TEMP_DIR,
            output_dir=GRAPH_DIR / slug,
        )

        lock = app.state.build_locks.setdefault(slug, asyncio.Lock())
        if not await lock.acquire():
            raise HTTPException(
                status_code=409, detail="Build already in progress for this repo"
            )
        try:
            app.state.build_status[slug] = {"status": "building", "progress": 0}
            try:
                result = await asyncio.to_thread(run_pipeline, config)
            except Exception as exc:
                app.state.build_status[slug] = {"status": "error", "progress": 0}
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            shutil.rmtree(TEMP_DIR / slug, ignore_errors=True)
            app.state.build_status[slug] = {"status": "done", "progress": 100}

            return BuildResponse(
                status="done",
                slug=slug,
                commit_hash=result.commit_hash,
                node_count=result.stats.get("nodes"),
                edge_count=result.stats.get("edges"),
                community_count=result.stats.get("communities"),
            )
        finally:
            lock.release()

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
    async def delete_repo(body: DeleteRepoRequest) -> dict[str, Any]:
        from codeknow.git_download import get_path, unregister
        from codeknow.pipeline import PipelineConfig
        from codeknow.vector.chroma import ChromaConfig, ChromaStore
        from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings

        url = body.url
        config = PipelineConfig(repo_url=url)
        slug = config.slug

        if get_path(url) is None:
            raise HTTPException(status_code=404, detail=f"Repo not found: {url}")

        shutil.rmtree(GRAPH_DIR / slug, ignore_errors=True)
        shutil.rmtree(TEMP_DIR / slug, ignore_errors=True)

        chunks_deleted = 0
        try:
            embeddings = create_embeddings(EmbeddingConfig())
            collection_name = config.chroma_collection or f"codeknow_{slug}"
            store = ChromaStore(
                config=ChromaConfig(
                    host=config.chroma_host,
                    port=config.chroma_port,
                    collection_name=collection_name,
                ),
                embeddings=embeddings,
            )
            chunks_deleted = store.delete_by_slug(slug)
        except Exception:
            logger.warning(
                "ChromaDB deletion failed for slug '%s'", slug, exc_info=True
            )

        unregister(url)
        return {"status": "deleted", "slug": slug, "chunks_deleted": chunks_deleted}

    @app.get("/v1/repos")
    async def list_repos(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        health_check: bool = Query(default=False),
    ) -> ListReposResponse:
        from codeknow.pipeline import load_metadata

        repos: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        if GRAPH_DIR.is_dir():
            for child in sorted(GRAPH_DIR.iterdir()):
                if not child.is_dir():
                    continue
                slug = child.name
                try:
                    meta = load_metadata(child)
                    if meta is None:
                        continue
                except Exception as exc:
                    errors.append({"slug": slug, "error": str(exc)})
                    continue

                build_info = app.state.build_status.get(slug)
                if build_info:
                    meta["build_status"] = build_info["status"]
                    meta["build_progress"] = build_info["progress"]

                if health_check:
                    try:
                        from codeknow.pipeline.io import load_graph

                        load_graph(child / "graph.json")
                        meta["health"] = "ok"
                    except FileNotFoundError:
                        meta["health"] = "missing_graph"
                    except Exception as exc:
                        meta["health"] = f"error: {exc}"

                repos.append(meta)

        start = (page - 1) * page_size
        end = start + page_size
        paged = repos[start:end]

        return ListReposResponse(
            repos=[RepoMetadata(**r) for r in paged],
            total=len(repos),
            page=page,
            page_size=page_size,
            errors=errors,
        )

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
