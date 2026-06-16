"""ASGI middleware that returns stub responses when CODEKNOW_STUB is set."""

from __future__ import annotations

import json
import os
import re
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

_Matcher = str | re.Pattern[str]


def _route_matches(matcher: _Matcher, path: str) -> bool:
    """Match a path against a literal route or a compiled template (S4)."""
    if isinstance(matcher, re.Pattern):
        return matcher.fullmatch(path) is not None
    return matcher == path


def _stub_build(_body: bytes, _qs: str) -> tuple[int, dict[str, Any]]:
    return (
        202,
        {
            "status": "queued",
            "slug": _STUB_REPO["slug"],
            "status_url": f"/v1/build/{_STUB_REPO['slug']}",
            "progress": 0,
        },
    )


def _stub_search(body: bytes, _qs: str) -> tuple[int, dict[str, Any]]:
    return (
        200,
        {
            "query": json.loads(body).get("query"),
            "vector_hits": 0,
            "graph_expanded": 0,
            "results": [],
        },
    )


def _stub_list_repos(_body: bytes, _qs: str) -> tuple[int, dict[str, Any]]:
    return (
        200,
        {
            "repos": [_STUB_REPO],
            "total": 1,
            "page": 1,
            "page_size": 50,
            "errors": [],
        },
    )


def _stub_build_status(_body: bytes, _qs: str) -> tuple[int, dict[str, Any]]:
    return 200, _STUB_BUILD_STATUS


# (method, matcher, handler) triples. ``matcher`` is a literal path or a
# compiled pattern, so the templated GET /v1/build/{slug} route is no longer
# special-cased (S4).
_STUB_ROUTES: list[tuple[str, _Matcher, _Handler]] = [
    ("POST", "/v1/build", _stub_build),
    ("POST", "/v1/search", _stub_search),
    ("DELETE", "/v1/repos", _stub_delete),
    ("GET", "/v1/repos", _stub_list_repos),
    ("GET", re.compile(r"/v1/build/[^/]+"), _stub_build_status),
]


async def _send_json(send: Send, status: int, payload: dict[str, Any]) -> None:
    """Emit a single JSON ASGI response (S3)."""
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [[b"content-type", b"application/json"]],
        }
    )
    await send({"type": "http.response.body", "body": body})


class StubMiddleware:
    """If CODEKNOW_STUB is truthy, intercept matching routes and return stub data."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.stub_mode = os.getenv("CODEKNOW_STUB", "").lower() in ("1", "true")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.stub_mode and scope["type"] == "http":
            method = scope["method"]
            path = scope["path"]
            for route_method, matcher, handler in _STUB_ROUTES:
                if route_method != method or not _route_matches(matcher, path):
                    continue
                body = await _read_body(receive)
                query_string = scope["query_string"].decode()
                status, response_body = handler(body, query_string)
                await _send_json(send, status, response_body)
                return

        await self.app(scope, receive, send)
