"""ASGI middleware that returns stub responses when CODEKNOW_STUB is set."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Awaitable
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
    body += message.get("body", b"")
    while message.get("more_body"):
        message = await receive()
        body += message.get("body", b"")
    return body


_STUB_ROUTES: dict[str, dict[str, Callable[[bytes, str], tuple[int, dict[str, Any]]]]] = {
    "POST": {
        "/v1/build": lambda body, qs: (
            202,
            {
                "status": "pending",
                "github_ssh_url": json.loads(body).get("github_ssh_url", ""),
            },
        ),
        "/v1/search": lambda body, qs: (
            200,
            {
                "results": [],
                "query": json.loads(body).get("query"),
                "total": 0,
            },
        ),
    },
    "DELETE": {
        "/v1/repos": lambda body, qs: (
            200,
            {
                "status": "deleted",
                "github_ssh_url": _extract_url(qs),
            },
        ),
    },
    "GET": {
        "/v1/repos": lambda body, qs: (200, {"repos": []}),
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
