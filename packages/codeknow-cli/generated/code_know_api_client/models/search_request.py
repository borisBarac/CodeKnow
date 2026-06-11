from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SearchRequest")


@_attrs_define
class SearchRequest:
    """
    Attributes:
        query (str):
        top_k (int | Unset):  Default: 10.
        repos (list[str] | None | Unset):
    """

    query: str
    top_k: int | Unset = 10
    repos: list[str] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        query = self.query

        top_k = self.top_k

        repos: list[str] | None | Unset
        if isinstance(self.repos, Unset):
            repos = UNSET
        elif isinstance(self.repos, list):
            repos = self.repos

        else:
            repos = self.repos

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "query": query,
            }
        )
        if top_k is not UNSET:
            field_dict["top_k"] = top_k
        if repos is not UNSET:
            field_dict["repos"] = repos

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        query = d.pop("query")

        top_k = d.pop("top_k", UNSET)

        def _parse_repos(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                repos_type_0 = cast(list[str], data)

                return repos_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None | Unset, data)

        repos = _parse_repos(d.pop("repos", UNSET))

        search_request = cls(
            query=query,
            top_k=top_k,
            repos=repos,
        )

        search_request.additional_properties = d
        return search_request

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
