from __future__ import annotations

from unittest.mock import patch

import pytest
from codeknow.service_checks import (
    check_chroma,
    check_docker_model_runner,
    check_ollama,
)


class TestCheckOllama:
    @patch("urllib.request.urlopen")
    def test_explicit_base_url_used(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.status = 200
        check_ollama("http://custom:9999")
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://custom:9999/api/tags"

    @patch("urllib.request.urlopen")
    def test_env_var_fallback(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://env-ollama:11434/v1")
        mock_urlopen.return_value.__enter__.return_value.status = 200
        check_ollama()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://env-ollama:11434/api/tags"

    @patch("urllib.request.urlopen")
    def test_default_when_no_arg_or_env(self, mock_urlopen, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        mock_urlopen.return_value.__enter__.return_value.status = 200
        check_ollama()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:11434/api/tags"

    @patch("urllib.request.urlopen")
    def test_strips_v2_suffix_from_env(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://env-ollama:11434/v2")
        mock_urlopen.return_value.__enter__.return_value.status = 200
        check_ollama()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://env-ollama:11434/api/tags"

    @patch("urllib.request.urlopen")
    def test_http_400_raises_connection_error(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.status = 500
        with pytest.raises(ConnectionError, match="Ollama returned HTTP 500"):
            check_ollama("http://localhost:11434")

    @patch("urllib.request.urlopen")
    def test_urlerror_raises_connection_error(self, mock_urlopen):
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("Connection refused")
        with pytest.raises(ConnectionError, match="Cannot reach Ollama"):
            check_ollama("http://localhost:11434")

    @patch("urllib.request.urlopen")
    def test_http_error_from_urlopen(self, mock_urlopen):
        from io import BytesIO
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            url="http://localhost:11434/api/tags",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=BytesIO(),
        )
        with pytest.raises(ConnectionError, match="Ollama returned HTTP 500"):
            check_ollama("http://localhost:11434")


class TestCheckDockerModelRunner:
    @patch("urllib.request.urlopen")
    def test_explicit_base_url_used(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.status = 200
        check_docker_model_runner("http://custom:12434")
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://custom:12434/engines/v1/models"

    @patch("urllib.request.urlopen")
    def test_env_var_fallback(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("DOCKER_MODEL_RUNNER_URL", "http://env-dmr:12434/engines/v1")
        mock_urlopen.return_value.__enter__.return_value.status = 200
        check_docker_model_runner()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://env-dmr:12434/engines/v1/models"

    @patch("urllib.request.urlopen")
    def test_default_when_no_arg_or_env(self, mock_urlopen, monkeypatch):
        monkeypatch.delenv("DOCKER_MODEL_RUNNER_URL", raising=False)
        mock_urlopen.return_value.__enter__.return_value.status = 200
        check_docker_model_runner()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:12434/engines/v1/models"

    @patch("urllib.request.urlopen")
    def test_strips_engines_v1_suffix_from_env(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("DOCKER_MODEL_RUNNER_URL", "http://env-dmr:12434/engines/v1")
        mock_urlopen.return_value.__enter__.return_value.status = 200
        check_docker_model_runner()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://env-dmr:12434/engines/v1/models"

    @patch("urllib.request.urlopen")
    def test_http_500_raises_connection_error(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.status = 500
        with pytest.raises(
            ConnectionError, match="Docker Model Runner returned HTTP 500"
        ):
            check_docker_model_runner("http://localhost:12434")

    @patch("urllib.request.urlopen")
    def test_urlerror_raises_connection_error(self, mock_urlopen):
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("Connection refused")
        with pytest.raises(ConnectionError, match="Cannot reach Docker Model Runner"):
            check_docker_model_runner("http://localhost:12434")


class TestCheckChroma:
    @patch("urllib.request.urlopen")
    def test_explicit_host_port_used(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.status = 200
        check_chroma("custom-host", 9999)
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://custom-host:9999/api/v2/heartbeat"

    @patch("urllib.request.urlopen")
    def test_env_var_fallback(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("CHROMA_HOST", "env-chroma")
        monkeypatch.setenv("CHROMA_PORT", "9000")
        mock_urlopen.return_value.__enter__.return_value.status = 200
        check_chroma()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://env-chroma:9000/api/v2/heartbeat"

    @patch("urllib.request.urlopen")
    def test_default_when_no_arg_or_env(self, mock_urlopen, monkeypatch):
        monkeypatch.delenv("CHROMA_HOST", raising=False)
        monkeypatch.delenv("CHROMA_PORT", raising=False)
        mock_urlopen.return_value.__enter__.return_value.status = 200
        check_chroma()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:8018/api/v2/heartbeat"

    @patch("urllib.request.urlopen")
    def test_http_400_raises_connection_error(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.status = 503
        with pytest.raises(ConnectionError, match="ChromaDB returned HTTP 503"):
            check_chroma("localhost", 8000)

    @patch("urllib.request.urlopen")
    def test_urlerror_raises_connection_error(self, mock_urlopen):
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("Connection refused")
        with pytest.raises(ConnectionError, match="Cannot reach ChromaDB"):
            check_chroma("localhost", 8000)

    @patch("urllib.request.urlopen")
    def test_http_error_from_urlopen(self, mock_urlopen):
        from io import BytesIO
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            url="http://localhost:8000/api/v2/heartbeat",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=BytesIO(),
        )
        with pytest.raises(ConnectionError, match="ChromaDB returned HTTP 503"):
            check_chroma("localhost", 8000)
