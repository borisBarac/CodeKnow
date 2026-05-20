from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.list_repos_response_errors_item import ListReposResponseErrorsItem
    from ..models.repo_metadata import RepoMetadata


T = TypeVar("T", bound="ListReposResponse")


@_attrs_define
class ListReposResponse:
    """
    Attributes:
        repos (list[RepoMetadata]):
        total (int):
        page (int):
        page_size (int):
        errors (list[ListReposResponseErrorsItem] | Unset):
    """

    repos: list[RepoMetadata]
    total: int
    page: int
    page_size: int
    errors: list[ListReposResponseErrorsItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        repos = []
        for repos_item_data in self.repos:
            repos_item = repos_item_data.to_dict()
            repos.append(repos_item)

        total = self.total

        page = self.page

        page_size = self.page_size

        errors: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.errors, Unset):
            errors = []
            for errors_item_data in self.errors:
                errors_item = errors_item_data.to_dict()
                errors.append(errors_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "repos": repos,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )
        if errors is not UNSET:
            field_dict["errors"] = errors

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.list_repos_response_errors_item import ListReposResponseErrorsItem
        from ..models.repo_metadata import RepoMetadata

        d = dict(src_dict)
        repos = []
        _repos = d.pop("repos")
        for repos_item_data in _repos:
            repos_item = RepoMetadata.from_dict(repos_item_data)

            repos.append(repos_item)

        total = d.pop("total")

        page = d.pop("page")

        page_size = d.pop("page_size")

        _errors = d.pop("errors", UNSET)
        errors: list[ListReposResponseErrorsItem] | Unset = UNSET
        if _errors is not UNSET:
            errors = []
            for errors_item_data in _errors:
                errors_item = ListReposResponseErrorsItem.from_dict(errors_item_data)

                errors.append(errors_item)

        list_repos_response = cls(
            repos=repos,
            total=total,
            page=page,
            page_size=page_size,
            errors=errors,
        )

        list_repos_response.additional_properties = d
        return list_repos_response

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
