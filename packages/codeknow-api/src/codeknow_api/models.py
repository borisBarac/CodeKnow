from __future__ import annotations

from pydantic import BaseModel, field_validator

from codeknow_api.params import is_valid_github_ssh_url


class BuildRequest(BaseModel):
    github_ssh_url: str

    @field_validator("github_ssh_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not is_valid_github_ssh_url(v):
            raise ValueError(
                "Invalid GitHub SSH URL (expected git@github.com:owner/repo[.git])"
            )
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
