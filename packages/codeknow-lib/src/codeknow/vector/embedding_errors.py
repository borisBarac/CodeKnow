from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import suppress
from typing import Any

_CONTEXT_LENGTH_MARKERS = (
    "available context size",
    "context length",
    "context_length",
    "context size",
    "context window",
    "context_length_exceeded",
    "maximum context",
    "max context",
    "too many tokens",
    "input is too long",
)

_CONTEXT_LENGTH_PATTERNS = (
    re.compile(r"\brequest\b.{0,120}\btokens\b.{0,120}\bexceed"),
    re.compile(r"\binput\b.{0,120}\btokens\b.{0,120}\bexceed"),
    re.compile(r"\bexceed\w*\b.{0,120}\bcontext\b"),
    re.compile(r"\bmaximum\b.{0,120}\btokens\b"),
)


def is_context_length_error(exc: Exception) -> bool:
    """Return whether a provider error specifically reports context overflow."""
    if _status_code(exc) != 400:
        return False

    details = " ".join(_iter_error_text_parts(exc)).casefold()
    return _looks_like_context_length_error(details)


def is_rate_limit_error(exc: Exception) -> bool:
    """Return whether a provider error is a 429 rate-limit response."""
    return _status_code(exc) == 429


def _status_code(exc: Exception) -> int | None:
    status_code = _getattr_or_none(exc, "status_code")
    if isinstance(status_code, int):
        return status_code

    response = _getattr_or_none(exc, "response")
    response_status_code = _getattr_or_none(response, "status_code")
    if isinstance(response_status_code, int):
        return response_status_code
    return None


def _looks_like_context_length_error(details: str) -> bool:
    return any(marker in details for marker in _CONTEXT_LENGTH_MARKERS) or any(
        pattern.search(details) for pattern in _CONTEXT_LENGTH_PATTERNS
    )


def _iter_error_text_parts(exc: Exception) -> Iterator[str]:
    yield str(exc)

    for attr in ("message", "code", "type", "body"):
        yield from _iter_value_text(_getattr_or_none(exc, attr))

    response = _getattr_or_none(exc, "response")
    if response is not None:
        yield from _iter_response_text(response)


def _iter_response_text(response: object) -> Iterator[str]:
    json_method = _getattr_or_none(response, "json")
    if callable(json_method):
        with suppress(Exception):
            yield from _iter_value_text(json_method())

    for attr in ("text", "content"):
        yield from _iter_value_text(_getattr_or_none(response, attr))


def _iter_value_text(value: Any) -> Iterator[str]:
    if value is None:
        return

    if isinstance(value, str):
        yield value
        return

    if isinstance(value, bytes):
        yield value.decode("utf-8", errors="replace")
        return

    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _iter_value_text(key)
            yield from _iter_value_text(item)
        return

    if isinstance(value, list | tuple | set | frozenset):
        for item in value:
            yield from _iter_value_text(item)
        return

    yield str(value)


def _getattr_or_none(value: object, attr: str) -> Any:
    try:
        return getattr(value, attr, None)
    except Exception:
        return None
