# ruff: noqa: T201
"""Lightweight health-checks for external services used by e2e tests.

Each function reads configuration from environment variables when called
without arguments.  When called with explicit arguments, those take
precedence (used by test_embeddings.py).

Environment variables are loaded via ``uv run --env-file e2e/.env.e2e``.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from urllib.error import URLError

_TIMEOUT = 3


def _die(msg: str) -> None:
    sys.stderr.write(f"\nERROR: {msg}\n\n")
    _exit(msg)


def _exit(msg: str) -> None:
    try:
        import pytest

        pytest.exit(msg, returncode=1)
    except ImportError:
        sys.exit(1)


def check_ollama(base_url: str | None = None) -> None:
    if base_url is None:
        raw = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        base_url = raw.rstrip("/").removesuffix("/v1")
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            if resp.status >= 400:
                _die(
                    f"Ollama returned HTTP {resp.status} at {url}.\n"
                    "Start Ollama:  ollama serve"
                )
    except (URLError, OSError) as exc:
        _die(f"Cannot reach Ollama at {url}: {exc}\nStart Ollama:  ollama serve")


def check_chroma(host: str | None = None, port: int | None = None) -> None:
    if host is None:
        host = os.environ.get("CHROMA_HOST", "localhost")
    if port is None:
        port = int(os.environ.get("CHROMA_PORT", "8000"))
    url = f"http://{host}:{port}/api/v2/heartbeat"
    try:
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            if resp.status >= 400:
                _die(f"ChromaDB returned HTTP {resp.status} at {url}.\n")
    except (URLError, OSError) as exc:
        _die(f"Cannot reach ChromaDB at {url}: {exc}\n")


if __name__ == "__main__":
    provider = os.environ.get("EMBEDDING_PROVIDER", "ollama")

    if provider == "ollama":
        print("Checking Ollama...")
        check_ollama()
        print("Ollama: OK")

    print("Checking ChromaDB...")
    check_chroma()
    print("ChromaDB: OK")

    print("\nAll services reachable.")
