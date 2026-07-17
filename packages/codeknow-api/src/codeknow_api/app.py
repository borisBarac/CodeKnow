"""FastAPI application factory and entry-point for the codeknow API service."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from codeknow.pipeline.facade import PipelineFacade

# Annotation-only import: kept inline because `from __future__ import
# annotations` turns it into a string, so it is never resolved at runtime.
from codeknow.schemas import ListReposResponse  # noqa: TC002
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from codeknow_api.cache import RedisService, cache_search, set_default_service
from codeknow_api.middleware import StubMiddleware
from codeknow_api.models import (
    BuildJob,
    BuildRequest,
    BuildStatusResponse,
    DeleteRepoRequest,
    DeleteRepoResponse,
    SearchRequest,
    SearchResponse,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(frozen=True)
class ApiConfig:
    """Environment-derived configuration resolved once per app instance (B6).

    Reading env vars at import time forced tests to monkeypatch module globals;
    resolving them here (in ``create_app``) lets tests inject an explicit config.
    """

    graph_dir: Path
    temp_dir: Path
    job_ttl: timedelta
    cache_ttl: int

    @classmethod
    def from_env(cls) -> ApiConfig:
        home = Path.home() / ".codeknow"
        return cls(
            graph_dir=Path(os.getenv("CODEKNOW_GRAPH_DIR", str(home / "graph"))),
            temp_dir=Path(os.getenv("CODEKNOW_TEMP_DIR", str(home / "temp"))),
            job_ttl=timedelta(
                seconds=int(os.getenv("CODEKNOW_JOB_TTL_SECONDS", str(60 * 60)))
            ),
            cache_ttl=int(os.getenv("CODEKNOW_CACHE_TTL", "300")),
        )


@dataclass
class AppState:
    """Mutable per-app build/redis state, accessed via a typed object (S8)."""

    build_jobs: dict[str, BuildJob] = field(default_factory=dict)
    builds_in_flight: set[str] = field(default_factory=set)
    build_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    redis_service: RedisService = field(default_factory=RedisService)


logger = logging.getLogger(__name__)


def _evict_completed_jobs(jobs: dict[str, BuildJob], job_ttl: timedelta) -> None:
    now = datetime.now(tz=timezone.utc)
    expired = [
        slug
        for slug, job in jobs.items()
        if job.is_terminal()
        and job.completed_at is not None
        and (now - job.completed_at) > job_ttl
    ]
    for slug in expired:
        del jobs[slug]


async def _run_build(
    *,
    state: AppState,
    slug: str,
    github_ssh_url: str,
    redis_service: RedisService,
    facade: PipelineFacade,
    force_rebuild: bool = False,
) -> None:
    """Run a single build to completion, updating its :class:`BuildJob`.

    Every code path sets a terminal status: the whole body sits inside the
    ``try`` so even a failure before the inner build (e.g. a missing job) is
    surfaced on the job rather than leaving it stuck in ``queued`` (B3). The
    ``builds_in_flight`` sentinel is always released in ``finally`` (B4).
    """
    job: BuildJob | None = None
    try:
        job = state.build_jobs[slug]
        job.status = "running"
        # Drop any cached search results for this slug as soon as the rebuild
        # starts, so a stale hit can't be served while the build is in flight.
        await redis_service.invalidate_for_slug(slug)
        loop = asyncio.get_running_loop()

        def on_progress(stage: str, percent: int, message: str) -> None:
            def _update() -> None:
                j = state.build_jobs.get(slug)
                if j is not None:
                    j.progress = percent
                    j.stage = stage
                    j.message = message

            loop.call_soon_threadsafe(_update)

        result = await asyncio.to_thread(
            facade.build,
            github_ssh_url,
            clean_first=force_rebuild,
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
        if job is not None:
            job.status = "failed"
            job.error = str(exc)
            job.completed_at = datetime.now(tz=timezone.utc)
        else:
            logger.exception("Build failed before job was initialised: %s", slug)
    finally:
        state.builds_in_flight.discard(slug)


def create_app(config: ApiConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    config = config or ApiConfig.from_env()
    redis_service = RedisService.from_env()
    # Seed the module-global so the ``@cache_search`` decorator resolves to the
    # same instance we invalidate against and close on shutdown (see B1).
    set_default_service(redis_service)
    state = AppState(redis_service=redis_service)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(facade.recover)
        yield
        await redis_service.close()

    app = FastAPI(
        title="CodeKnow API",
        version="0.1.0",
        description="Knowledge graph service for code",
        lifespan=_lifespan,
    )
    app.add_middleware(StubMiddleware)
    app.state.codeknow = state

    facade = PipelineFacade(graph_dir=config.graph_dir, temp_dir=config.temp_dir)

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

        in_flight = state.builds_in_flight
        if slug in in_flight:
            raise HTTPException(
                status_code=409, detail="Build already in progress for this repo"
            )
        in_flight.add(slug)

        try:
            _evict_completed_jobs(state.build_jobs, config.job_ttl)
            state.build_jobs[slug] = BuildJob(slug=slug)
            task = asyncio.create_task(
                _run_build(
                    state=state,
                    slug=slug,
                    github_ssh_url=body.github_ssh_url,
                    redis_service=redis_service,
                    facade=facade,
                    force_rebuild=body.force_rebuild,
                )
            )
            state.build_tasks.add(task)
            task.add_done_callback(state.build_tasks.discard)
        except Exception:
            in_flight.discard(slug)
            raise

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
        _evict_completed_jobs(state.build_jobs, config.job_ttl)
        job = state.build_jobs.get(slug)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Build not found: {slug}")

        job_data = asdict(job)
        job_data.pop("completed_at", None)
        resp_data = BuildStatusResponse(**job_data).model_dump(exclude_none=True)

        if job.status in ("queued", "running"):
            return JSONResponse(
                content=resp_data,
                status_code=202,
                headers={"Retry-After": "3"},
            )

        return JSONResponse(content=resp_data, status_code=200)

    @app.post("/v1/search")
    @cache_search(ttl=config.cache_ttl)
    async def search(body: SearchRequest) -> SearchResponse:
        repos = body.repos

        if repos is not None:
            missing = [s for s in repos if not facade.has_slug(s)]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown slugs: {missing}",
                )

        try:
            result = await asyncio.to_thread(
                facade.search,
                body.query,
                top_k=body.top_k,
                slugs=repos,
            )
        except ValueError as exc:
            # Query/slug/top_k are validated upstream, so a ValueError here is a
            # server-side failure (e.g. a graph that won't load), not a client
            # error — surface it as 500 instead of miscategorising as 422.
            logger.warning("Search failed", exc_info=True)
            raise HTTPException(status_code=500, detail="Search failed") from exc

        return SearchResponse(**result.model_dump())

    @app.delete("/v1/repos")
    async def delete_repo(body: DeleteRepoRequest) -> DeleteRepoResponse:
        try:
            slug = body.resolve_slug()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not facade.has_slug(slug):
            raise HTTPException(status_code=404, detail=f"Repo not found: {slug}")
        if slug in state.builds_in_flight:
            raise HTTPException(
                status_code=409,
                detail=f"Build already in progress for repo: {slug}",
            )

        result = await asyncio.to_thread(facade.delete, slug)

        return DeleteRepoResponse(
            status="deleted",
            slug=result.slug,
            chunks_deleted=result.chunks_deleted,
        )

    @app.get("/v1/repos")
    async def list_repos(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
        health_check: bool = False,
    ) -> ListReposResponse:
        build_status_map: dict[str, dict[str, Any]] = {}
        for slug, job in state.build_jobs.items():
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
