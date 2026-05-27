"""Tests for codeknow_api.middleware — StubMiddleware and helpers."""

from __future__ import annotations

import json
from collections.abc import Callable  # noqa: TC003
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable

import pytest
from codeknow_api.middleware import (
    _STUB_REPO,
    Receive,
    Scope,
    Send,
    StubMiddleware,
    _read_body,
)


class TestReadBody:
    @staticmethod
    def _make_receive(messages: list[dict[str, Any]]) -> Receive:
        gen = iter(messages)

        async def receive() -> dict[str, Any]:
            return next(gen)

        return receive

    @pytest.mark.anyio
    async def test_single_message(self) -> None:
        receive = self._make_receive([{"body": b"hello"}])
        result = await _read_body(receive)
        assert result == b"hello"

    @pytest.mark.anyio
    async def test_chunked_messages(self) -> None:
        receive = self._make_receive(
            [
                {"body": b"hel", "more_body": True},
                {"body": b"lo"},
            ]
        )
        result = await _read_body(receive)
        assert result == b"hello"

    @pytest.mark.anyio
    async def test_empty_body(self) -> None:
        receive = self._make_receive([{"body": b""}])
        result = await _read_body(receive)
        assert result == b""


def _make_scope(method: str, path: str, qs: str = "") -> Scope:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": qs.encode(),
    }


async def _collect_send() -> tuple[list[dict[str, Any]], Send]:
    collected: list[dict[str, Any]] = []

    async def send(msg: dict[str, Any]) -> None:
        collected.append(msg)

    return collected, send


async def _make_inner_app(
    calls: list[Scope],
) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"inner"})

    return inner


def _body_receive(body: bytes) -> Receive:
    done = False

    async def receive() -> dict[str, Any]:
        nonlocal done
        if done:
            return {"body": b""}
        done = True
        return {"body": body}

    return receive


class TestStubMiddleware:
    @pytest.fixture(autouse=True)
    def _stub_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEKNOW_STUB", "1")

    @pytest.mark.anyio
    async def test_intercepts_post_build(self) -> None:
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)
        assert mw.stub_mode is True

        body = json.dumps({"github_ssh_url": "git@github.com:owner/repo.git"}).encode()
        scope = _make_scope("POST", "/v1/build")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(body), send)

        assert calls == []
        assert collected[0]["status"] == 202
        resp = json.loads(collected[1]["body"])
        assert resp["status"] == "done"
        assert resp["slug"] == _STUB_REPO["slug"]
        assert resp["commit_hash"] == _STUB_REPO["commit_hash"]
        assert resp["node_count"] == _STUB_REPO["node_count"]
        assert resp["edge_count"] == _STUB_REPO["edge_count"]
        assert resp["community_count"] == _STUB_REPO["community_count"]

    @pytest.mark.anyio
    async def test_intercepts_post_search(self) -> None:
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)

        body = json.dumps({"query": "find auth"}).encode()
        scope = _make_scope("POST", "/v1/search")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(body), send)

        assert calls == []
        assert collected[0]["status"] == 200
        resp = json.loads(collected[1]["body"])
        assert resp["query"] == "find auth"
        assert resp["vector_hits"] == 0
        assert resp["graph_expanded"] == 0
        assert resp["results"] == []

    @pytest.mark.anyio
    async def test_intercepts_delete_repos(self) -> None:
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)

        body = json.dumps({"url": "git@github.com:stub/repo.git"}).encode()
        scope = _make_scope("DELETE", "/v1/repos")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(body), send)

        assert calls == []
        assert collected[0]["status"] == 200
        resp = json.loads(collected[1]["body"])
        assert resp["status"] == "deleted"
        assert resp["slug"] == _STUB_REPO["slug"]
        assert resp["chunks_deleted"] == 0

    @pytest.mark.anyio
    async def test_intercepts_get_repos(self) -> None:
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)

        scope = _make_scope("GET", "/v1/repos")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(b""), send)

        assert calls == []
        resp = json.loads(collected[1]["body"])
        assert resp == {
            "repos": [_STUB_REPO],
            "total": 1,
            "page": 1,
            "page_size": 50,
            "errors": [],
        }

    @pytest.mark.anyio
    async def test_passthrough_when_stub_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CODEKNOW_STUB", raising=False)
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)
        assert mw.stub_mode is False

        scope = _make_scope("GET", "/v1/repos")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(b""), send)

        assert len(calls) == 1
        assert collected[1]["body"] == b"inner"

    @pytest.mark.anyio
    async def test_passthrough_for_unknown_path(self) -> None:
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)

        scope = _make_scope("GET", "/v1/unknown")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(b""), send)

        assert len(calls) == 1
        assert collected[1]["body"] == b"inner"

    @pytest.mark.anyio
    async def test_passthrough_for_non_http_scope(self) -> None:
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)

        scope = {"type": "lifespan"}
        _collected, send = await _collect_send()
        await mw(scope, _body_receive(b""), send)

        assert len(calls) == 1

    @pytest.mark.anyio
    async def test_stub_mode_truthy_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEKNOW_STUB", "true")
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)
        assert mw.stub_mode is True

        scope = _make_scope("GET", "/v1/repos")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(b""), send)

        assert calls == []
        resp = json.loads(collected[1]["body"])
        assert resp == {
            "repos": [_STUB_REPO],
            "total": 1,
            "page": 1,
            "page_size": 50,
            "errors": [],
        }

    @pytest.mark.anyio
    async def test_stub_mode_falsy_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEKNOW_STUB", "0")
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)
        assert mw.stub_mode is False

        scope = _make_scope("GET", "/v1/repos")
        _collected, send = await _collect_send()
        await mw(scope, _body_receive(b""), send)

        assert len(calls) == 1

    @pytest.mark.anyio
    async def test_response_content_type_is_json(self) -> None:
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)

        scope = _make_scope("GET", "/v1/repos")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(b""), send)

        headers = collected[0]["headers"]
        assert [b"content-type", b"application/json"] in headers
