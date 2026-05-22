from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from codeknow_api.params import is_valid_github_ssh_url


class BuildRequest(BaseModel):
    github_ssh_url: str

    @field_validator("github_ssh_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not is_valid_github_ssh_url(v):
            msg = "Invalid GitHub SSH URL (expected git@github.com:owner/repo[.git])"
            raise ValueError(msg)
        return v


class BuildResponse(BaseModel):
    status: str
    slug: str | None = None
    commit_hash: str | None = None
    node_count: int | None = None
    edge_count: int | None = None
    community_count: int | None = None


class DeleteRepoRequest(BaseModel):
    url: str


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
            return stripped
        return v


class SearchResponse(BaseModel):
    query: str
    vector_hits: int
    graph_expanded: int
    results: list[dict]
