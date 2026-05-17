"""FastAPI application factory and entry-point for the codeknow API service."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from pydantic import BaseModel


class QueryRequest(BaseModel):
    question: str
    mode: str = "bfs"
    max_depth: int = 3


class PathRequest(BaseModel):
    source: str
    target: str


class ExplainRequest(BaseModel):
    node_id: str


class ChunksRequest(BaseModel):
    node_ids: list[str]


class BuildRequest(BaseModel):
    source: str
    mode: str = "full"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="CodeKnow API",
        version="0.1.0",
        description="Knowledge graph service for code",
    )
    app.state.build_status = {"status": "idle", "progress": 0}

    @app.post("/v1/graph/query")
    async def graph_query(req: QueryRequest) -> dict[str, Any]:
        return {"nodes": [], "edges": [], "question": req.question}

    @app.post("/v1/graph/path")
    async def graph_path(req: PathRequest) -> dict[str, Any]:
        return {"path": [], "source": req.source, "target": req.target}

    @app.post("/v1/graph/explain")
    async def graph_explain(req: ExplainRequest) -> dict[str, Any]:
        return {
            "node_id": req.node_id,
            "community": None,
            "neighbors": [],
            "chunks": [],
        }

    @app.post("/v1/graph/chunks")
    async def graph_chunks(req: ChunksRequest) -> dict[str, Any]:
        return {"chunks": {}, "node_ids": req.node_ids}

    @app.post("/v1/graph/build", status_code=202)
    async def graph_build(req: BuildRequest, request: Request) -> dict[str, Any]:
        request.app.state.build_status = {"status": "pending", "progress": 0}
        return {"status": "pending", "source": req.source, "mode": req.mode}

    @app.get("/v1/graph/status")
    async def graph_status(request: Request) -> dict[str, Any]:
        return dict(request.app.state.build_status)

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
