"""Thin API client for the codeknow daemon."""

from __future__ import annotations

DEFAULT_BASE_URL = "http://127.0.0.1:9999"


class Client:
    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = base_url

    def check_daemon(self) -> bool:
        raise NotImplementedError

    def add_to_index(self, ssh_url: str) -> dict:
        raise NotImplementedError

    def search(self, query: str, slug: str | None = None) -> dict:
        raise NotImplementedError

    def remove_from_index(self, slug: str) -> dict:
        raise NotImplementedError
