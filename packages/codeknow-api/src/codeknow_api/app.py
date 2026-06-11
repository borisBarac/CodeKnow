"""FastAPI application factory and entry-point for the codeknow API service."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from codeknow.pipeline.facade import PipelineFacade
from fastapi import FastAPI, HTTPException, Query

from codeknow_api.cache import cache_search, close_redis, invalidate_for_slug
from codeknow_api.middleware import StubMiddleware
from codeknow_api.models import (
    BuildRequest,
    BuildResponse,
    DeleteRepoRequest,
    SearchRequest,
    SearchResponse,
)

if TYPE_CHECKING:
    from codeknow.schemas import ListReposResponse

_CODEKNOW_HOME = Path.home() / ".codeknow"
GRAPH_DIR = Path(os.getenv("CODEKNOW_GRAPH_DIR", str(_CODEKNOW_HOME / "graph")))
TEMP_DIR = Path(os.getenv("CODEKNOW_TEMP_DIR", str(_CODEKNOW_HOME / "temp")))

_facade = PipelineFacade(graph_dir=GRAPH_DIR, temp_dir=TEMP_DIR)

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
    build_locks: dict[str, asyncio.Lock] = {}
    app.state.build_locks = build_locks

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await close_redis()

    def _check_import(
        module_path: str, names: tuple[str, ...]
    ) -> dict[str, str] | None:
        try:
            mod = __import__(module_path, fromlist=names)
            for name in names:
                getattr(mod, name)
        except Exception as exc:
            return {"module": module_path, "error": str(exc)}
        return None

    @app.get("/health")
    async def health() -> dict[str, Any]:
        lazy_imports = (
            ("codeknow.pipeline", ("PipelineFacade", "run_pipeline")),
            ("codeknow.pipeline.io", ("load_graph",)),
            ("codeknow.vector.chroma", ("ChromaConfig", "ChromaStore")),
            ("codeknow.vector.embeddings", ("EmbeddingConfig", "create_embeddings")),
            ("codeknow.vector.search", ("GraphSearcher",)),
            ("codeknow.git_download", ("get_path", "unregister")),
        )
        checks = [
            r for r in (_check_import(m, n) for m, n in lazy_imports) if r is not None
        ]
        if checks:
            raise HTTPException(
                status_code=503,
                detail={"status": "unhealthy", "errors": checks},
            )
        return {"status": "ok"}

    @app.post("/v1/build", status_code=202)
    async def build(body: BuildRequest) -> BuildResponse:
        slug = _facade.resolve_slug(body.github_ssh_url)

        lock = app.state.build_locks.setdefault(slug, asyncio.Lock())
        if not await lock.acquire():
            raise HTTPException(
                status_code=409, detail="Build already in progress for this repo"
            )
        try:
            app.state.build_status[slug] = {"status": "building", "progress": 0}

            try:
                result = await asyncio.to_thread(
                    _facade.build,
                    body.github_ssh_url,
                    clean_first=True,
                )
            except Exception as exc:
                app.state.build_status[slug] = {"status": "error", "progress": 0}
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            app.state.build_status[slug] = {"status": "done", "progress": 100}
            await invalidate_for_slug(slug)

            return BuildResponse(
                status="done",
                slug=result.slug,
                commit_hash=result.commit_hash,
                node_count=result.node_count,
                edge_count=result.edge_count,
                community_count=result.community_count,
            )
        finally:
            lock.release()

    @app.post("/v1/search")
    @cache_search()
    async def search(body: SearchRequest) -> SearchResponse:
        repos = body.repos

        if repos is not None:
            missing = [s for s in repos if not _facade.has_slug(s)]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown slugs: {missing}",
                )

            building = [
                s
                for s in repos
                if app.state.build_status.get(s, {}).get("status") == "building"
            ]
            if building:
                raise HTTPException(
                    status_code=409,
                    detail=f"Repos being rebuilt: {building}",
                )

        try:
            result = await asyncio.to_thread(
                _facade.search,
                body.query,
                top_k=body.top_k,
                slugs=repos,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return SearchResponse(**result.model_dump())

    @app.delete("/v1/repos")
    async def delete_repo(body: DeleteRepoRequest) -> dict[str, Any]:
        try:
            slug = body.resolve_slug()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        url = body.url
        if url is None:
            url = _facade.resolve_url_for_slug(slug)

        if not _facade.has_slug(slug):
            if url is not None:
                from codeknow.git_download import get_path

                if get_path(url) is None:
                    raise HTTPException(
                        status_code=404, detail=f"Repo not found: {slug}"
                    )
            else:
                raise HTTPException(status_code=404, detail=f"Repo not found: {slug}")

        result = await asyncio.to_thread(_facade.delete, slug)

        return {
            "status": "deleted",
            "slug": result.slug,
            "chunks_deleted": result.chunks_deleted,
        }

    @app.get("/v1/repos")
    async def list_repos(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
        health_check: Annotated[bool, Query()] = False,
    ) -> ListReposResponse:
        build_status = dict(app.state.build_status)
        return await asyncio.to_thread(
            _facade.list_repos,
            page=page,
            page_size=page_size,
            health_check=health_check,
            build_status=build_status,
        )

    return app


def main() -> None:
    """Run the API server."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="CodeKnow API server")
    parser.add_argument(
        "--host",
        default=os.getenv("CODEKNOW_API_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CODEKNOW_API_PORT", "8080")),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug mode (auto-reload + debug logging)",
    )
    args = parser.parse_args()
    uvicorn.run(
        "codeknow_api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.debug,
        log_level="debug" if args.debug else "info",
    )
