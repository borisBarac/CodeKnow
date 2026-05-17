"""Param verification utilities for the codeknow API."""

from __future__ import annotations

import re

_GITHUB_SSH_RE = re.compile(r"^git@github\.com:[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(\.git)?$")


def is_valid_github_ssh_url(url: str) -> bool:
    """Return ``True`` if *url* is a valid GitHub SSH URL."""
    return bool(_GITHUB_SSH_RE.match(url))


def validate_github_ssh_url(url: str) -> None:
    """Raise :class:`ValueError` if *url* is not a valid GitHub SSH URL."""
    if not is_valid_github_ssh_url(url):
        raise ValueError(
            f"Invalid GitHub SSH URL: {url!r}  "
            "(expected git@github.com:owner/repo[.git])"
        )
