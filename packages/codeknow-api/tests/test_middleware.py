"""Tests for codeknow_api.middleware — StubMiddleware and helpers."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from codeknow_api.middleware import (
    Receive,
    Scope,
    Send,
    StubMiddleware,
    _extract_url,
    _read_body,
)

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class TestExtractUrl:
    def test_returns_url_from_query_string(self) -> None:
        qs = "url=git%40github.com%3Aowner%2Frepo"
        assert _extract_url(qs) == "git@github.com:owner/repo"

    def test_returns_first_value_when_multiple(self) -> None:
        qs = "url=first&url=second"
        assert _extract_url(qs) == "first"

    def test_returns_empty_when_no_url_param(self) -> None:
        assert _extract_url("") == ""
        assert _extract_url("foo=bar") == ""


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
    @pytest.mark.anyio
    async def test_intercepts_post_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEKNOW_STUB", "1")
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
        assert resp["slug"] == "stub-owner-stub-repo"
        assert resp["commit_hash"] == "a" * 40
        assert resp["node_count"] == 0
        assert resp["edge_count"] == 0
        assert resp["community_count"] == 0
        assert resp["github_ssh_url"] == "git@github.com:owner/repo.git"

    @pytest.mark.anyio
    async def test_intercepts_post_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEKNOW_STUB", "1")
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
    async def test_intercepts_delete_repos(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEKNOW_STUB", "1")
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)

        body = json.dumps({"url": "git@github.com:owner/repo.git"}).encode()
        scope = _make_scope("DELETE", "/v1/repos")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(body), send)

        assert calls == []
        assert collected[0]["status"] == 200
        resp = json.loads(collected[1]["body"])
        assert resp["status"] == "deleted"
        assert resp["slug"] == "stub-owner-stub-repo"
        assert resp["chunks_deleted"] == 0
        assert resp["github_ssh_url"] == "git@github.com:owner/repo.git"

    @pytest.mark.anyio
    async def test_intercepts_get_repos(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEKNOW_STUB", "1")
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)

        scope = _make_scope("GET", "/v1/repos")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(b""), send)

        assert calls == []
        resp = json.loads(collected[1]["body"])
        assert resp == {"repos": []}

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
    async def test_passthrough_for_unknown_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEKNOW_STUB", "1")
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)

        scope = _make_scope("GET", "/v1/unknown")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(b""), send)

        assert len(calls) == 1
        assert collected[1]["body"] == b"inner"

    @pytest.mark.anyio
    async def test_passthrough_for_non_http_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEKNOW_STUB", "1")
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)

        scope = {"type": "lifespan"}
        collected, send = await _collect_send()
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
        assert resp == {"repos": []}

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
        collected, send = await _collect_send()
        await mw(scope, _body_receive(b""), send)

        assert len(calls) == 1

    @pytest.mark.anyio
    async def test_response_content_type_is_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEKNOW_STUB", "1")
        calls: list[Scope] = []
        inner = await _make_inner_app(calls)
        mw = StubMiddleware(inner)

        scope = _make_scope("GET", "/v1/repos")
        collected, send = await _collect_send()
        await mw(scope, _body_receive(b""), send)

        headers = collected[0]["headers"]
        assert [b"content-type", b"application/json"] in headers
