from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator, model_validator

from codeknow_api.params import validate_github_ssh_url

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class BuildJob:
    slug: str
    status: str = "queued"
    progress: int = 0
    stage: str | None = None
    message: str | None = None
    error: str | None = None
    commit_hash: str | None = None
    node_count: int | None = None
    edge_count: int | None = None
    community_count: int | None = None
    completed_at: datetime | None = None

    def is_terminal(self) -> bool:
        return self.status in ("succeeded", "failed")


class BuildRequest(BaseModel):
    github_ssh_url: str
    force_rebuild: bool = False

    @field_validator("github_ssh_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        validate_github_ssh_url(v)
        return v


class BuildStatusResponse(BaseModel):
    status: str
    slug: str
    status_url: str | None = None
    progress: int = 0
    stage: str | None = None
    message: str | None = None
    error: str | None = None
    commit_hash: str | None = None
    node_count: int | None = None
    edge_count: int | None = None
    community_count: int | None = None


class DeleteRepoRequest(BaseModel):
    url: str | None = None
    slug: str | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> DeleteRepoRequest:
        if self.url is None and self.slug is None:
            msg = "Either 'url' or 'slug' must be provided"
            raise ValueError(msg)
        return self

    def resolve_slug(self) -> str:
        if self.slug:
            return self.slug
        # _at_least_one_field guarantees url is set whenever slug is not.
        assert self.url is not None  # noqa: S101 - invariant, not a runtime check
        from codeknow.pipeline.facade import PipelineFacade

        return PipelineFacade.resolve_slug(self.url)


class DeleteRepoResponse(BaseModel):
    status: str
    slug: str
    chunks_deleted: int


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    repos: list[str] | None = None

    @field_validator("query")
    @classmethod
    def _validate_query(cls, v: str) -> str:
        if not v.strip():
            msg = "query must be a non-empty string"
            raise ValueError(msg)
        return v

    @field_validator("repos")
    @classmethod
    def _validate_repos(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            stripped = [s.strip() for s in v]
            if any(not s for s in stripped):
                msg = "repos must contain non-empty slug strings"
                raise ValueError(msg)
            return list(dict.fromkeys(stripped))
        return v


class SearchResponse(BaseModel):
    query: str
    vector_hits: int
    graph_expanded: int
    results: list[dict]
