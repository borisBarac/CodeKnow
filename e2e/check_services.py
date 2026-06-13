# ruff: noqa: T201
"""Lightweight health-checks for external services used by e2e tests.

Each function delegates to :mod:`codeknow.service_checks` and converts the
raised :exc:`ConnectionError` into a fatal exit (appropriate for e2e pre-flight
but not for library code).  When called with explicit arguments those take
precedence (used by test_embeddings.py).

Environment variables are loaded via ``uv run --env-file e2e/.env.e2e``.
"""

from __future__ import annotations

import os
import sys


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
    from codeknow.service_checks import check_ollama as _check

    try:
        _check(base_url)
    except ConnectionError as exc:
        _die(str(exc))


def check_docker_model_runner(base_url: str | None = None) -> None:
    from codeknow.service_checks import check_docker_model_runner as _check

    try:
        _check(base_url)
    except ConnectionError as exc:
        _die(str(exc))


def check_chroma(host: str | None = None, port: int | None = None) -> None:
    from codeknow.service_checks import check_chroma as _check

    try:
        _check(host, port)
    except ConnectionError as exc:
        _die(str(exc))


if __name__ == "__main__":
    provider = os.environ.get("EMBEDDING_PROVIDER", "docker")

    if provider == "docker":
        print("Checking Docker Model Runner...")
        check_docker_model_runner()
        print("Docker Model Runner: OK")
    elif provider == "ollama":
        print("Checking Ollama...")
        check_ollama()
        print("Ollama: OK")

    print("Checking ChromaDB...")
    check_chroma()
    print("ChromaDB: OK")

    print("\nAll services reachable.")
