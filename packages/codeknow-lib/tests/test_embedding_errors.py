from __future__ import annotations

from codeknow.vector.embedding_errors import (
    is_context_length_error,
    is_rate_limit_error,
    is_transient_embedding_error,
)


class BadRequestError(Exception):
    status_code = 400


def test_matches_real_available_context_size_message():
    exc = BadRequestError(
        "Error code: 400\n"
        "request (2189 tokens) exceeds the available context size (2048 tokens)"
    )

    assert is_context_length_error(exc)


def test_matches_structured_provider_body_code():
    class StructuredBadRequestError(Exception):
        status_code = 400

        def __init__(self) -> None:
            super().__init__("provider failed")
            self.body = {
                "error": {
                    "code": "context_length_exceeded",
                    "message": "input is too large",
                }
            }

    assert is_context_length_error(StructuredBadRequestError())


def test_rejects_unrelated_bad_request():
    exc = BadRequestError("model not found")

    assert not is_context_length_error(exc)


def test_rejects_context_message_without_bad_request_status():
    exc = RuntimeError("context length exceeded")

    assert not is_context_length_error(exc)


def test_matches_rate_limit_status_code():
    class RateLimitError(Exception):
        status_code = 429

    assert is_rate_limit_error(RateLimitError("too many requests"))


def test_rejects_non_rate_limit_status_code():
    class NotRateLimitedError(Exception):
        status_code = 400

    assert not is_rate_limit_error(NotRateLimitedError("bad request"))


def test_matches_transient_server_error():
    class ServerError(Exception):
        status_code = 500

    assert is_transient_embedding_error(ServerError("provider failed"))


def test_matches_dmr_llama_process_crash():
    exc = RuntimeError("llama.cpp terminated unexpectedly: llama.cpp failed")

    assert is_transient_embedding_error(exc)


def test_rejects_non_transient_client_error():
    assert not is_transient_embedding_error(BadRequestError("model not found"))
