"""ASGI middleware that returns stub responses when CODEKNOW_STUB is set."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any, cast
from urllib.parse import parse_qs

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _extract_url(query_string: str) -> str:
    params = parse_qs(query_string)
    return params.get("url", [""])[0]


async def _read_body(receive: Receive) -> bytes:
    body = b""
    message = await receive()
    body += cast("bytes", message.get("body", b""))
    while message.get("more_body"):
        message = await receive()
        body += cast("bytes", message.get("body", b""))
    return body


_Handler = Callable[[bytes, str], tuple[int, dict[str, Any]]]

_STUB_ROUTES: dict[str, dict[str, _Handler]] = {
    "POST": {
        "/v1/build": lambda body, _qs: (
            202,
            {
                "status": "done",
                "slug": "stub-owner-stub-repo",
                "commit_hash": "a" * 40,
                "node_count": 0,
                "edge_count": 0,
                "community_count": 0,
                "github_ssh_url": json.loads(body).get("github_ssh_url", ""),
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
        "/v1/repos": lambda body, _qs: (
            200,
            {
                "status": "deleted",
                "slug": "stub-owner-stub-repo",
                "chunks_deleted": 0,
                "github_ssh_url": json.loads(body).get("url", ""),
            },
        ),
    },
    "GET": {
        "/v1/repos": lambda _body, _qs: (200, {"repos": []}),
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

        await self.app(scope, receive, send)
