from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.search_response_results_item import SearchResponseResultsItem


T = TypeVar("T", bound="SearchResponse")


@_attrs_define
class SearchResponse:
    """
    Attributes:
        query (str):
        vector_hits (int):
        graph_expanded (int):
        results (list[SearchResponseResultsItem]):
    """

    query: str
    vector_hits: int
    graph_expanded: int
    results: list[SearchResponseResultsItem]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        query = self.query

        vector_hits = self.vector_hits

        graph_expanded = self.graph_expanded

        results = []
        for results_item_data in self.results:
            results_item = results_item_data.to_dict()
            results.append(results_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "query": query,
                "vector_hits": vector_hits,
                "graph_expanded": graph_expanded,
                "results": results,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.search_response_results_item import SearchResponseResultsItem

        d = dict(src_dict)
        query = d.pop("query")

        vector_hits = d.pop("vector_hits")

        graph_expanded = d.pop("graph_expanded")

        results = []
        _results = d.pop("results")
        for results_item_data in _results:
            results_item = SearchResponseResultsItem.from_dict(results_item_data)

            results.append(results_item)

        search_response = cls(
            query=query,
            vector_hits=vector_hits,
            graph_expanded=graph_expanded,
            results=results,
        )

        search_response.additional_properties = d
        return search_response

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
