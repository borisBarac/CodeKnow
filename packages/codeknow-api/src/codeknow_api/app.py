"""FastAPI application factory and entry-point for the codeknow API service."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from codeknow.pipeline.facade import PipelineFacade
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

if TYPE_CHECKING:
    from codeknow.schemas import ListReposResponse

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
        from codeknow.pipeline import PipelineConfig, run_pipeline

        slug = PipelineConfig(repo_url=body.github_ssh_url).slug
        config = PipelineConfig(
            repo_url=body.github_ssh_url,
            input_dir=TEMP_DIR,
            output_dir=GRAPH_DIR / slug,
        )

        lock = app.state.build_locks.setdefault(slug, asyncio.Lock())
        if lock.locked():
            raise HTTPException(
                status_code=409, detail="Build already in progress for this repo"
            )

        await lock.acquire()

        if (GRAPH_DIR / slug).exists():
            shutil.rmtree(GRAPH_DIR / slug, ignore_errors=True)
            shutil.rmtree(TEMP_DIR / slug, ignore_errors=True)
            try:
                from codeknow.vector.chroma import ChromaConfig, ChromaStore
                from codeknow.vector.embeddings import (
                    EmbeddingConfig,
                    create_embeddings,
                )

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
                store.delete_by_slug(slug)
            except Exception:
                logger.warning(
                    "ChromaDB cleanup failed for slug '%s'", slug, exc_info=True
                )

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
                    run_pipeline, config, progress_callback=on_progress
                )
                job.status = "succeeded"
                job.progress = 100
                job.commit_hash = result.commit_hash
                job.node_count = result.stats.get("nodes")
                job.edge_count = result.stats.get("edges")
                job.community_count = result.stats.get("communities")
                job.completed_at = datetime.now(tz=timezone.utc)
                await redis_service.invalidate_for_slug(slug)
                shutil.rmtree(TEMP_DIR / slug, ignore_errors=True)
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
            missing = [s for s in repos if not _facade.has_slug(s)]
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
            if url is None:
                raise HTTPException(status_code=404, detail=f"Repo not found: {slug}")
            from codeknow.git_download import get_path

            if get_path(url) is None:
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

                build_job = app.state.build_jobs.get(slug)
                if build_job is not None:
                    meta["build_status"] = build_job.status
                    meta["build_progress"] = build_job.progress

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
