"""Custom exceptions for the codeknow CLI."""

from __future__ import annotations


class CodeknowError(Exception):
    """Base exception for all codeknow CLI errors."""


class DaemonNotRunningError(CodeknowError):
    """Raised when the daemon is unreachable or not started."""


class DaemonTimeoutError(CodeknowError):
    """Raised when the daemon does not respond within the expected time."""


class DaemonAlreadyRunningError(CodeknowError):
    """Raised when trying to start a daemon that is already running."""


class ApiError(CodeknowError):
    """Raised on unexpected API status codes."""


class ValidationError(CodeknowError):
    """Raised when the API returns a 422 validation error."""


class RepoNotFoundError(CodeknowError):
    """Raised when a requested repo slug or URL is not found."""


class RepoConflictError(CodeknowError):
    """Raised on 409 conflict (e.g. repo already being built)."""


class ConfigError(CodeknowError):
    """Raised when required tools or configuration are missing."""
