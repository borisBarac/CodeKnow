"""ASGI middleware that returns stub responses when CODEKNOW_STUB is set."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any, cast

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


async def _read_body(receive: Receive) -> bytes:
    body = b""
    message = await receive()
    body += cast("bytes", message.get("body", b""))
    while message.get("more_body"):
        message = await receive()
        body += cast("bytes", message.get("body", b""))
    return body


_STUB_REPO = {
    "github_ssh_url": "git@github.com:stub/repo.git",
    "slug": "stub-repo",
    "commit_hash": "a" * 40,
    "built_at": "2025-01-01T00:00:00Z",
    "node_count": 10,
    "edge_count": 20,
    "community_count": 2,
}

_Handler = Callable[[bytes, str], tuple[int, dict[str, Any]]]


def _stub_delete(body: bytes, _qs: str) -> tuple[int, dict[str, Any]]:
    data = json.loads(body) if body else {}
    url = data.get("url") or ""
    from codeknow.pipeline.facade import PipelineFacade

    slug = data.get("slug") or (PipelineFacade.resolve_slug(url) if url else "")
    if slug != _STUB_REPO["slug"]:
        return 404, {"detail": f"Repo not found: {slug}"}
    return (
        200,
        {
            "status": "deleted",
            "slug": _STUB_REPO["slug"],
            "chunks_deleted": 0,
        },
    )


_STUB_BUILD_STATUS: dict[str, Any] = {
    "status": "succeeded",
    "slug": _STUB_REPO["slug"],
    "progress": 100,
    "commit_hash": _STUB_REPO["commit_hash"],
    "node_count": _STUB_REPO["node_count"],
    "edge_count": _STUB_REPO["edge_count"],
    "community_count": _STUB_REPO["community_count"],
}

_STUB_ROUTES: dict[str, dict[str, _Handler]] = {
    "POST": {
        "/v1/build": lambda _body, _qs: (
            202,
            {
                "status": "queued",
                "slug": _STUB_REPO["slug"],
                "status_url": f"/v1/build/{_STUB_REPO['slug']}",
                "progress": 0,
            },
        ),
        "/v1/search": lambda body, _qs: (
            200,
            {
                "query": json.loads(body).get("query"),
                "vector_hits": 0,
                "graph_expanded": 0,
                "results": [],
            },
        ),
    },
    "DELETE": {
        "/v1/repos": _stub_delete,
    },
    "GET": {
        "/v1/repos": lambda _body, _qs: (
            200,
            {
                "repos": [_STUB_REPO],
                "total": 1,
                "page": 1,
                "page_size": 50,
                "errors": [],
            },
        ),
    },
}


class StubMiddleware:
    """If CODEKNOW_STUB is truthy, intercept matching routes and return stub data."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.stub_mode = os.getenv("CODEKNOW_STUB", "").lower() in ("1", "true")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.stub_mode and scope["type"] == "http":
            method = scope["method"]
            path = scope["path"]
            handlers = _STUB_ROUTES.get(method)
            if handlers and path in handlers:
                body = await _read_body(receive)
                query_string = scope.get("query_string", b"").decode()
                status, response_body = handlers[path](body, query_string)
                payload = json.dumps(response_body).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": status,
                        "headers": [
                            [b"content-type", b"application/json"],
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": payload,
                    }
                )
                return

            if method == "GET" and path.startswith("/v1/build/"):
                payload = json.dumps(_STUB_BUILD_STATUS).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            [b"content-type", b"application/json"],
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": payload,
                    }
                )
                return

        await self.app(scope, receive, send)
