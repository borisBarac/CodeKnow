"""Thin API client for the codeknow daemon."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NoReturn, ParamSpec, TypeVar

import httpx
from code_know_api_client import errors as api_errors
from code_know_api_client.api.default import (
    build_v1_build_post,
    delete_repo_v1_repos_delete,
    list_repos_v1_repos_get,
    search_v1_search_post,
)
from code_know_api_client.client import Client as GeneratedClient
from code_know_api_client.models import (
    delete_repo_v1_repos_delete_response_delete_repo_v1_repos_delete as _del_resp,
)
from code_know_api_client.models import (
    search_v1_search_post_response_search_v1_search_post as _search_resp,
)
from code_know_api_client.models.build_request import BuildRequest
from code_know_api_client.models.build_response import BuildResponse
from code_know_api_client.models.delete_repo_request import DeleteRepoRequest
from code_know_api_client.models.http_validation_error import HTTPValidationError
from code_know_api_client.models.list_repos_response import ListReposResponse
from code_know_api_client.models.search_v1_search_post_body import (
    SearchV1SearchPostBody,
)
from code_know_api_client.types import Unset

from codeknow_cli.daemon_manager import DaemonManager
from codeknow_cli.endpoint import resolve_endpoint
from codeknow_cli.exceptions import (
    ApiError,
    DaemonNotRunningError,
    RepoConflictError,
    RepoNotFoundError,
    ValidationError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_P = ParamSpec("_P")
_R = TypeVar("_R")


@dataclass
class SearchHit:
    file: str
    start_line: int | None
    end_line: int | None
    provenance: str
    distance: float | None = None
    weight: float | None = None
    slug: str | None = None
    graph_path: str | None = None
    content: str = ""


@dataclass
class SearchResult:
    query: str
    vector_hits: int
    graph_expanded: int
    results: list[SearchHit] = field(default_factory=list)


@dataclass
class DeleteResult:
    status: str
    slug: str
    chunks_deleted: int


@dataclass
class DaemonStartResult:
    status: str
    pid: int | None = None


@dataclass
class DaemonStopResult:
    status: str


class Client:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        pid_file: str | None = None,
    ) -> None:
        cfg = resolve_endpoint(host, port, pid_file)

        self.base_url = cfg.base_url
        self.host = cfg.host
        self.port = cfg.port
        self._bind_host = cfg.bind_host
        self._pid_file = cfg.pid_file
        self._remote = cfg.is_remote

        self._daemon_pid: int | None = None
        self._manager: DaemonManager | None = None
        if cfg.worker_command is not None:
            self._manager = DaemonManager(
                pid_file=cfg.pid_file,
                worker_command=cfg.worker_command,
            )

        self._api_client = GeneratedClient(
            base_url=self.base_url,
            raise_on_unexpected_status=True,
            timeout=httpx.Timeout(300.0),
        )

    @property
    def is_remote(self) -> bool:
        return self._remote

    def _require_manager(self) -> DaemonManager:
        if self._manager is None:
            msg = "No local daemon manager available"
            raise DaemonNotRunningError(msg)
        return self._manager

    def start_daemon(self, timeout: float = 5.0) -> DaemonStartResult:
        if self._remote:
            print("You are in remote mode")  # noqa: T201
            return DaemonStartResult(status="remote")
        manager = self._require_manager()
        pid = manager.start()
        self._daemon_pid = pid
        self._wait_for_ready(timeout)
        return DaemonStartResult(status="started", pid=pid)

    def stop_daemon(self, timeout: float = 5.0) -> DaemonStopResult:
        if self._remote:
            print("You are in remote mode")  # noqa: T201
            return DaemonStopResult(status="remote")
        manager = self._require_manager()
        manager.stop(timeout=timeout)
        self._daemon_pid = None
        return DaemonStopResult(status="stopped")

    def check_daemon(self) -> bool:
        if self._remote:
            return False
        return self._require_manager().is_running()

    def get_daemon_pid(self) -> int | None:
        if self._remote:
            return None
        return self._require_manager().read_pid()

    def _call_api(
        self, fn: Callable[_P, _R], *args: _P.args, **kwargs: _P.kwargs
    ) -> _R:
        try:
            return fn(*args, **kwargs)
        except httpx.TransportError as exc:
            if self._remote:
                msg = f"Cannot connect to the API at {self.base_url}"
            else:
                msg = (
                    "Cannot connect to the daemon. Start it with: codeknow daemon start"
                )
            raise DaemonNotRunningError(msg) from exc

    def _raise_for_unexpected_status(
        self, exc: api_errors.UnexpectedStatus
    ) -> NoReturn:
        body = exc.content.decode(errors="ignore")
        msg = f"Unexpected API status {exc.status_code}: {body}"
        raise ApiError(msg) from exc

    def _raise_validation_or_error(self, resp: Any, fallback_msg: str) -> NoReturn:
        if resp.status_code == 422 and isinstance(resp.parsed, HTTPValidationError):
            detail = resp.parsed.detail
            if not isinstance(detail, Unset) and detail:
                msgs = [str(d) for d in detail]
                msg = f"Validation error: {', '.join(msgs)}"
                raise ValidationError(msg)
            raise ValidationError(fallback_msg)
        msg = f"Unexpected response from API (status {resp.status_code})"
        raise ApiError(msg)

    def add_to_index(self, ssh_url: str) -> BuildResponse:
        try:
            resp = self._call_api(
                build_v1_build_post.sync_detailed,
                client=self._api_client,
                body=BuildRequest(github_ssh_url=ssh_url),
            )
        except api_errors.UnexpectedStatus as exc:
            if exc.status_code == 409:
                msg = "Repo is already being built"
                raise RepoConflictError(msg) from exc
            self._raise_for_unexpected_status(exc)

        if resp.status_code == 202 and isinstance(resp.parsed, BuildResponse):
            return resp.parsed
        self._raise_validation_or_error(resp, "Invalid GitHub SSH URL")
        return None

    @staticmethod
    def _extract_detail(content: bytes) -> str:
        try:
            data: dict[str, Any] = json.loads(content)
            result: Any = data.get("detail", "")
            return str(result) if result else ""
        except (json.JSONDecodeError, AttributeError):
            return content.decode(errors="ignore")

    def search(self, query: str, slugs: list[str] | None = None) -> SearchResult:
        body = SearchV1SearchPostBody()
        body["query"] = query
        body["top_k"] = 10
        if slugs:
            body["repos"] = slugs

        try:
            resp = self._call_api(
                search_v1_search_post.sync_detailed,
                client=self._api_client,
                body=body,
            )
        except api_errors.UnexpectedStatus as exc:
            if exc.status_code == 400:
                detail = self._extract_detail(exc.content)
                raise RepoNotFoundError(
                    f"Unknown slugs: {detail}" if detail else "Unknown slugs"
                ) from exc
            if exc.status_code == 409:
                detail = self._extract_detail(exc.content)
                raise RepoConflictError(
                    f"Repos being rebuilt: {detail}"
                    if detail
                    else "Repos being rebuilt"
                ) from exc
            self._raise_for_unexpected_status(exc)

        if resp.status_code == 200 and isinstance(
            resp.parsed, _search_resp.SearchV1SearchPostResponseSearchV1SearchPost
        ):
            raw = dict(resp.parsed.additional_properties)
            return SearchResult(
                query=raw.get("query", ""),
                vector_hits=raw.get("vector_hits", 0),
                graph_expanded=raw.get("graph_expanded", 0),
                results=[
                    SearchHit(
                        file=h.get("file", "?"),
                        start_line=h.get("start_line"),
                        end_line=h.get("end_line"),
                        provenance=h.get("provenance", "unknown"),
                        distance=h.get("distance"),
                        weight=h.get("weight"),
                        slug=h.get("slug"),
                        graph_path=h.get("graph_path"),
                        content=h.get("content", ""),
                    )
                    for h in raw.get("results", [])
                ],
            )

        self._raise_validation_or_error(resp, "Invalid request")
        return None

    def remove_from_index(self, slug: str) -> DeleteResult:
        try:
            del_resp = self._call_api(
                delete_repo_v1_repos_delete.sync_detailed,
                client=self._api_client,
                body=DeleteRepoRequest(slug=slug),
            )
        except api_errors.UnexpectedStatus as exc:
            if exc.status_code == 404:
                msg = f"Repo with slug '{slug}' not found"
                raise RepoNotFoundError(msg) from exc
            self._raise_for_unexpected_status(exc)

        if del_resp.status_code == 200 and isinstance(
            del_resp.parsed,
            _del_resp.DeleteRepoV1ReposDeleteResponseDeleteRepoV1ReposDelete,
        ):
            raw = dict(del_resp.parsed.additional_properties)
            return DeleteResult(
                status=raw.get("status", "deleted"),
                slug=raw.get("slug", slug),
                chunks_deleted=raw.get("chunks_deleted", 0),
            )

        self._raise_validation_or_error(del_resp, "Validation error deleting repo")
        return None

    def list_repos(self) -> ListReposResponse:
        try:
            resp = self._call_api(
                list_repos_v1_repos_get.sync_detailed,
                client=self._api_client,
            )
        except api_errors.UnexpectedStatus as exc:
            self._raise_for_unexpected_status(exc)

        if resp.status_code == 200 and isinstance(resp.parsed, ListReposResponse):
            return resp.parsed

        self._raise_validation_or_error(resp, "Validation error listing repos")
        return None

    def _wait_for_ready(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(f"{self.base_url}/v1/repos", timeout=1.0)
                if resp.status_code == 200:
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            time.sleep(0.1)
        msg = "Daemon did not become ready within timeout"
        raise DaemonNotRunningError(msg)
