"""FastAPI application factory and entry-point for the codeknow API service."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Query

from codeknow_api.middleware import StubMiddleware


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="CodeKnow API",
        version="0.1.0",
        description="Knowledge graph service for code",
    )
    app.add_middleware(StubMiddleware)
    app.state.build_status = {"status": "idle", "progress": 0}

    @app.post("/v1/build", status_code=202)
    async def build(body: dict[str, Any]) -> dict[str, Any]:
        github_ssh_url = body.get("github_ssh_url", "")
        app.state.build_status = {"status": "pending", "progress": 0}
        return {"status": "pending", "github_ssh_url": github_ssh_url}

    @app.post("/v1/search")
    async def search(body: dict[str, Any]) -> dict[str, Any]:
        query = body.get("query")
        top_k = body.get("top_k", 10)
        return {"results": [], "query": query, "total": 0}

    @app.delete("/v1/repos")
    async def delete_repo(url: str = Query(...)) -> dict[str, Any]:
        return {"status": "deleted", "github_ssh_url": url}

    @app.get("/v1/repos")
    async def list_repos() -> dict[str, Any]:
        return {"repos": []}

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
