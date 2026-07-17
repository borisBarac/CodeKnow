"""Tests for GET /health endpoint."""

from __future__ import annotations

import builtins
from unittest.mock import patch

import pytest
from codeknow_api.app import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


class TestHealthEndpoint:
    def test_startup_recovers_abandoned_generations(self) -> None:
        with (
            patch("codeknow.pipeline.facade.PipelineFacade.recover") as recover,
            TestClient(create_app()),
        ):
            pass

        recover.assert_called_once_with()

    def test_returns_200_when_imports_succeed(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_returns_503_when_import_fails(self, client: TestClient) -> None:
        real_import = builtins.__import__

        def _failing_import(
            name: str,
            _globals: object | None = None,
            _locals: object | None = None,
            fromlist: object = (),
            level: int = 0,
        ) -> object:
            if name == "codeknow.vector.chroma":
                msg = "no module"
                raise ImportError(msg)
            return real_import(name, _globals, _locals, fromlist, level)

        with patch("builtins.__import__", side_effect=_failing_import):
            resp = client.get("/health")

        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"]["status"] == "unhealthy"
        errors = body["detail"]["errors"]
        assert len(errors) == 1
        assert errors[0]["module"] == "codeknow.vector.chroma"

    def test_checks_all_lazy_modules(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checked: list[str] = []
        real_import = builtins.__import__

        def _tracking_import(
            name: str,
            _globals: object | None = None,
            _locals: object | None = None,
            fromlist: object = (),
            level: int = 0,
        ) -> object:
            if name.startswith("codeknow."):
                checked.append(name)
            return real_import(name, _globals, _locals, fromlist, level)

        with patch("builtins.__import__", side_effect=_tracking_import):
            client.get("/health")

        assert "codeknow.pipeline" in checked
        assert "codeknow.pipeline.io" in checked
        assert "codeknow.vector.chroma" in checked
        assert "codeknow.vector.embeddings" in checked
        assert "codeknow.vector.search" in checked
        assert "codeknow.git_download" in checked
