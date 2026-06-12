"""FastAPI application factory and entry-point for the codeknow API service."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from codeknow.pipeline.facade import PipelineFacade
from codeknow.schemas import ListReposResponse  # noqa: TC002
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from codeknow_api.cache import RedisService, cache_search
from codeknow_api.middleware import StubMiddleware
from codeknow_api.models import (
    BuildJob,
    BuildRequest,
    BuildStatusResponse,
    DeleteRepoRequest,
    SearchRequest,
    SearchResponse,
)

_CODEKNOW_HOME = Path.home() / ".codeknow"
GRAPH_DIR = Path(os.getenv("CODEKNOW_GRAPH_DIR", str(_CODEKNOW_HOME / "graph")))
TEMP_DIR = Path(os.getenv("CODEKNOW_TEMP_DIR", str(_CODEKNOW_HOME / "temp")))

JOB_TTL = timedelta(seconds=int(os.getenv("CODEKNOW_JOB_TTL_SECONDS", str(60 * 60))))

logger = logging.getLogger(__name__)


def _evict_completed_jobs(jobs: dict[str, BuildJob]) -> None:
    now = datetime.now(tz=timezone.utc)
    expired = [
        slug
        for slug, job in jobs.items()
        if job.is_terminal()
        and job.completed_at is not None
        and (now - job.completed_at) > JOB_TTL
    ]
    for slug in expired:
        del jobs[slug]


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="CodeKnow API",
        version="0.1.0",
        description="Knowledge graph service for code",
    )
    app.add_middleware(StubMiddleware)
    build_jobs: dict[str, BuildJob] = {}
    app.state.build_jobs = build_jobs  # type: ignore[assignment]
    build_locks: dict[str, asyncio.Lock] = {}
    app.state.build_locks = build_locks  # type: ignore[assignment]
    redis_service = RedisService.from_env()
    app.state.redis_service = redis_service  # type: ignore[assignment]

    facade = PipelineFacade(graph_dir=GRAPH_DIR, temp_dir=TEMP_DIR)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await redis_service.close()

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

    @app.post("/v1/build")
    async def build(body: BuildRequest) -> JSONResponse:
        slug = facade.resolve_slug(body.github_ssh_url)

        lock = app.state.build_locks.setdefault(slug, asyncio.Lock())
        if lock.locked():
            raise HTTPException(
                status_code=409, detail="Build already in progress for this repo"
            )

        await lock.acquire()

        _evict_completed_jobs(app.state.build_jobs)
        app.state.build_jobs[slug] = BuildJob(slug=slug)

        async def _run_build() -> None:
            loop = asyncio.get_running_loop()
            job = app.state.build_jobs[slug]
            job.status = "running"

            def on_progress(stage: str, percent: int, message: str) -> None:
                def _update() -> None:
                    j = app.state.build_jobs.get(slug)
                    if j is not None:
                        j.progress = percent
                        j.stage = stage
                        j.message = message

                loop.call_soon_threadsafe(_update)

            try:
                result = await asyncio.to_thread(
                    facade.build,
                    body.github_ssh_url,
                    clean_first=True,
                    progress_callback=on_progress,
                )
                job.status = "succeeded"
                job.progress = 100
                job.commit_hash = result.commit_hash
                job.node_count = result.node_count
                job.edge_count = result.edge_count
                job.community_count = result.community_count
                job.completed_at = datetime.now(tz=timezone.utc)
                await redis_service.invalidate_for_slug(slug)
            except Exception as exc:
                job.status = "failed"
                job.error = str(exc)
                job.completed_at = datetime.now(tz=timezone.utc)
            finally:
                lock.release()

        _build_task = asyncio.create_task(_run_build())  # noqa: RUF006

        return JSONResponse(
            content=BuildStatusResponse(
                status="queued",
                slug=slug,
                status_url=f"/v1/build/{slug}",
                progress=0,
            ).model_dump(exclude_none=True),
            status_code=202,
            headers={
                "Location": f"/v1/build/{slug}",
                "Retry-After": "3",
            },
        )

    @app.get("/v1/build/{slug}")
    async def build_status(slug: str) -> JSONResponse:
        _evict_completed_jobs(app.state.build_jobs)
        job = app.state.build_jobs.get(slug)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Build not found: {slug}")

        resp_data = BuildStatusResponse(**job.to_response_dict()).model_dump(
            exclude_none=True
        )

        if job.status in ("queued", "running"):
            return JSONResponse(
                content=resp_data,
                status_code=202,
                headers={"Retry-After": "3"},
            )

        return JSONResponse(content=resp_data, status_code=200)

    @app.post("/v1/search")
    @cache_search()
    async def search(body: SearchRequest) -> SearchResponse:
        repos = body.repos

        if repos is not None:
            missing = [s for s in repos if not facade.has_slug(s)]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown slugs: {missing}",
                )

            building = [
                s
                for s in repos
                if app.state.build_jobs.get(s) is not None
                and app.state.build_jobs[s].status in ("queued", "running")
            ]
            if building:
                raise HTTPException(
                    status_code=409,
                    detail=f"Repos being rebuilt: {building}",
                )

        try:
            result = await asyncio.to_thread(
                facade.search,
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
            url = facade.resolve_url_for_slug(slug)

        if not facade.has_slug(slug):
            if url is None:
                raise HTTPException(status_code=404, detail=f"Repo not found: {slug}")
            if facade.get_repo_path(url) is None:
                raise HTTPException(status_code=404, detail=f"Repo not found: {slug}")

        result = await asyncio.to_thread(facade.delete, slug)

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
        build_status_map: dict[str, dict[str, Any]] = {}
        for slug, job in app.state.build_jobs.items():
            build_status_map[slug] = {
                "status": job.status,
                "progress": job.progress,
            }

        return facade.list_repos(
            page=page,
            page_size=page_size,
            health_check=health_check,
            build_status=build_status_map,
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
