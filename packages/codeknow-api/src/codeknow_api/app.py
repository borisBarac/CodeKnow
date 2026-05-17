"""FastAPI application factory and entry-point for the codeknow API service."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
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

    build_status: dict[str, Any] = {"status": "idle", "progress": 0}

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
    async def graph_build(req: BuildRequest) -> dict[str, Any]:
        build_status["status"] = "pending"
        build_status["progress"] = 0
        return {"status": "pending", "source": req.source, "mode": req.mode}

    @app.get("/v1/graph/status")
    async def graph_status() -> dict[str, Any]:
        return build_status

    return app


def main() -> None:
    """Run the API server."""
    import uvicorn

    uvicorn.run(
        "codeknow_api.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8080,
    )
