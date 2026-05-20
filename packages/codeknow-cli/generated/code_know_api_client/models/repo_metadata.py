from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="RepoMetadata")


@_attrs_define
class RepoMetadata:
    """
    Attributes:
        github_ssh_url (str):
        slug (str):
        commit_hash (str):
        built_at (str):
        node_count (int):
        edge_count (int):
        community_count (int):
        health (None | str | Unset):
        build_status (None | str | Unset):
        build_progress (int | None | Unset):
    """

    github_ssh_url: str
    slug: str
    commit_hash: str
    built_at: str
    node_count: int
    edge_count: int
    community_count: int
    health: None | str | Unset = UNSET
    build_status: None | str | Unset = UNSET
    build_progress: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        github_ssh_url = self.github_ssh_url

        slug = self.slug

        commit_hash = self.commit_hash

        built_at = self.built_at

        node_count = self.node_count

        edge_count = self.edge_count

        community_count = self.community_count

        health: None | str | Unset
        if isinstance(self.health, Unset):
            health = UNSET
        else:
            health = self.health

        build_status: None | str | Unset
        if isinstance(self.build_status, Unset):
            build_status = UNSET
        else:
            build_status = self.build_status

        build_progress: int | None | Unset
        if isinstance(self.build_progress, Unset):
            build_progress = UNSET
        else:
            build_progress = self.build_progress

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "github_ssh_url": github_ssh_url,
                "slug": slug,
                "commit_hash": commit_hash,
                "built_at": built_at,
                "node_count": node_count,
                "edge_count": edge_count,
                "community_count": community_count,
            }
        )
        if health is not UNSET:
            field_dict["health"] = health
        if build_status is not UNSET:
            field_dict["build_status"] = build_status
        if build_progress is not UNSET:
            field_dict["build_progress"] = build_progress

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        github_ssh_url = d.pop("github_ssh_url")

        slug = d.pop("slug")

        commit_hash = d.pop("commit_hash")

        built_at = d.pop("built_at")

        node_count = d.pop("node_count")

        edge_count = d.pop("edge_count")

        community_count = d.pop("community_count")

        def _parse_health(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        health = _parse_health(d.pop("health", UNSET))

        def _parse_build_status(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        build_status = _parse_build_status(d.pop("build_status", UNSET))

        def _parse_build_progress(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        build_progress = _parse_build_progress(d.pop("build_progress", UNSET))

        repo_metadata = cls(
            github_ssh_url=github_ssh_url,
            slug=slug,
            commit_hash=commit_hash,
            built_at=built_at,
            node_count=node_count,
            edge_count=edge_count,
            community_count=community_count,
            health=health,
            build_status=build_status,
            build_progress=build_progress,
        )

        repo_metadata.additional_properties = d
        return repo_metadata

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
