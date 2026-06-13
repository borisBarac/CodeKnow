"""Health-checks for external services (Docker Model Runner, Ollama, ChromaDB).

These functions raise :exc:`ConnectionError` on failure so callers can decide
whether to exit, retry, or degrade gracefully.
"""

from __future__ import annotations

import os
import urllib.request
from urllib.error import HTTPError, URLError

DEFAULT_CHROMA_HOST = "localhost"
DEFAULT_CHROMA_PORT = 8018
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_DMR_BASE_URL = "http://localhost:12434"

_TIMEOUT = 3


def check_docker_model_runner(base_url: str | None = None) -> None:
    """Verify Docker Model Runner (DMR) is reachable.

    Parameters
    ----------
    base_url:
        Root URL of the DMR host (e.g. ``http://localhost:12434``).
        If omitted, falls back to the ``DOCKER_MODEL_RUNNER_URL`` environment
        variable (any ``/engines/v1`` suffix is stripped), then
        ``http://localhost:12434``.

    Raises
    ------
    ConnectionError
        If DMR returned a non-2xx status or is unreachable.

    """
    if base_url is None:
        raw = os.environ.get("DOCKER_MODEL_RUNNER_URL", DEFAULT_DMR_BASE_URL)
        base_url = raw.rstrip("/").removesuffix("/engines/v1")
    url = f"{base_url.rstrip('/')}/engines/v1/models"
    resp_status: int | None = None
    try:
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            resp_status = resp.status
    except HTTPError as exc:
        msg = (
            f"Docker Model Runner returned HTTP {exc.code} at {url}. "
            "Ensure DMR is enabled:  docker desktop enable model-runner"
        )
        raise ConnectionError(msg) from exc
    except (URLError, OSError) as exc:
        msg = (
            f"Cannot reach Docker Model Runner at {url}: {exc}. "
            "Ensure DMR is enabled:  docker desktop enable model-runner"
        )
        raise ConnectionError(msg) from exc

    if resp_status is not None and resp_status >= 400:
        msg = (
            f"Docker Model Runner returned HTTP {resp_status} at {url}. "
            "Ensure DMR is enabled:  docker desktop enable model-runner"
        )
        raise ConnectionError(msg)


def check_ollama(base_url: str | None = None) -> None:
    """Verify Ollama is reachable.

    Parameters
    ----------
    base_url:
        Full URL (e.g. ``http://localhost:11434``).
        If omitted, falls back to the ``OLLAMA_BASE_URL`` environment variable
        (any ``/v1`` or ``/v2`` suffix is stripped), then
        ``http://localhost:11434``.

    Raises
    ------
    ConnectionError
        If Ollama returned a non-2xx status or is unreachable.

    """
    if base_url is None:
        raw = os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
        base_url = raw.rstrip("/").removesuffix("/v1").removesuffix("/v2")
    url = f"{base_url.rstrip('/')}/api/tags"
    resp_status: int | None = None
    try:
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            resp_status = resp.status
    except HTTPError as exc:
        msg = f"Ollama returned HTTP {exc.code} at {url}. Start Ollama:  ollama serve"
        raise ConnectionError(msg) from exc
    except (URLError, OSError) as exc:
        msg = f"Cannot reach Ollama at {url}: {exc}. Start Ollama:  ollama serve"
        raise ConnectionError(msg) from exc

    if resp_status is not None and resp_status >= 400:
        msg = (
            f"Ollama returned HTTP {resp_status} at {url}. Start Ollama:  ollama serve"
        )
        raise ConnectionError(msg)


def check_chroma(host: str | None = None, port: int | None = None) -> None:
    """Verify ChromaDB HTTP server is reachable.

    Parameters
    ----------
    host:
        Hostname. Falls back to the ``CHROMA_HOST`` environment variable,
        then ``localhost``.
    port:
        Port number. Falls back to the ``CHROMA_PORT`` environment variable,
        then ``8018``.

    Raises
    ------
    ConnectionError
        If ChromaDB returned a non-2xx status or is unreachable.

    """
    resolved_host = (
        host if host is not None else os.environ.get("CHROMA_HOST", DEFAULT_CHROMA_HOST)
    )
    if port is not None:
        resolved_port = port
    else:
        resolved_port = int(os.environ.get("CHROMA_PORT", str(DEFAULT_CHROMA_PORT)))
    url = f"http://{resolved_host}:{resolved_port}/api/v2/heartbeat"
    resp_status: int | None = None
    try:
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            resp_status = resp.status
    except HTTPError as exc:
        msg = f"ChromaDB returned HTTP {exc.code} at {url}."
        raise ConnectionError(msg) from exc
    except (URLError, OSError) as exc:
        msg = f"Cannot reach ChromaDB at {url}: {exc}."
        raise ConnectionError(msg) from exc

    if resp_status is not None and resp_status >= 400:
        msg = f"ChromaDB returned HTTP {resp_status} at {url}."
        raise ConnectionError(msg)
