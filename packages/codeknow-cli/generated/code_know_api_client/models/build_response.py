from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BuildResponse")


@_attrs_define
class BuildResponse:
    """
    Attributes:
        status (str):
        slug (None | str | Unset):
        commit_hash (None | str | Unset):
        node_count (int | None | Unset):
        edge_count (int | None | Unset):
        community_count (int | None | Unset):
    """

    status: str
    slug: None | str | Unset = UNSET
    commit_hash: None | str | Unset = UNSET
    node_count: int | None | Unset = UNSET
    edge_count: int | None | Unset = UNSET
    community_count: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status = self.status

        slug: None | str | Unset
        if isinstance(self.slug, Unset):
            slug = UNSET
        else:
            slug = self.slug

        commit_hash: None | str | Unset
        if isinstance(self.commit_hash, Unset):
            commit_hash = UNSET
        else:
            commit_hash = self.commit_hash

        node_count: int | None | Unset
        if isinstance(self.node_count, Unset):
            node_count = UNSET
        else:
            node_count = self.node_count

        edge_count: int | None | Unset
        if isinstance(self.edge_count, Unset):
            edge_count = UNSET
        else:
            edge_count = self.edge_count

        community_count: int | None | Unset
        if isinstance(self.community_count, Unset):
            community_count = UNSET
        else:
            community_count = self.community_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
            }
        )
        if slug is not UNSET:
            field_dict["slug"] = slug
        if commit_hash is not UNSET:
            field_dict["commit_hash"] = commit_hash
        if node_count is not UNSET:
            field_dict["node_count"] = node_count
        if edge_count is not UNSET:
            field_dict["edge_count"] = edge_count
        if community_count is not UNSET:
            field_dict["community_count"] = community_count

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        status = d.pop("status")

        def _parse_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        slug = _parse_slug(d.pop("slug", UNSET))

        def _parse_commit_hash(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        commit_hash = _parse_commit_hash(d.pop("commit_hash", UNSET))

        def _parse_node_count(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        node_count = _parse_node_count(d.pop("node_count", UNSET))

        def _parse_edge_count(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        edge_count = _parse_edge_count(d.pop("edge_count", UNSET))

        def _parse_community_count(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        community_count = _parse_community_count(d.pop("community_count", UNSET))

        build_response = cls(
            status=status,
            slug=slug,
            commit_hash=commit_hash,
            node_count=node_count,
            edge_count=edge_count,
            community_count=community_count,
        )

        build_response.additional_properties = d
        return build_response

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
