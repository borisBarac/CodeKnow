from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="BuildRequest")


@_attrs_define
class BuildRequest:
    """
    Attributes:
        github_ssh_url (str):
        force_rebuild (bool):
        fetch_remote (bool):
    """

    github_ssh_url: str
    force_rebuild: bool = False
    fetch_remote: bool = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        github_ssh_url = self.github_ssh_url
        force_rebuild = self.force_rebuild
        fetch_remote = self.fetch_remote

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "github_ssh_url": github_ssh_url,
                "force_rebuild": force_rebuild,
                "fetch_remote": fetch_remote,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        github_ssh_url = d.pop("github_ssh_url")
        force_rebuild = d.pop("force_rebuild", False)
        fetch_remote = d.pop("fetch_remote", True)

        build_request = cls(
            github_ssh_url=github_ssh_url,
            force_rebuild=force_rebuild,
            fetch_remote=fetch_remote,
        )

        build_request.additional_properties = d
        return build_request

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
