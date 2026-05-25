from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text

_CONTENT_TRUNCATE = 200


def _build_file_label(hit: dict[str, Any]) -> str:
    file_loc = hit.get("file", "?")
    start_line = hit.get("start_line")
    end_line = hit.get("end_line")
    if start_line is not None and end_line is not None:
        return f"{file_loc}:{start_line}-{end_line}"
    return str(file_loc)


def _build_provenance_label(hit: dict[str, Any]) -> str:
    provenance = hit.get("provenance", "unknown")
    distance = hit.get("distance")
    weight = hit.get("weight")
    if distance is not None:
        return f"{provenance} (distance: {distance})"
    if weight is not None:
        return f"{provenance} (weight: {weight})"
    return str(provenance)


def _truncate_content(content: str) -> str:
    if len(content) > _CONTENT_TRUNCATE:
        return content[:_CONTENT_TRUNCATE] + "..."
    return content


def format_search_results(query: str, result: dict[str, Any]) -> None:
    console = Console(force_terminal=None)
    is_tty = console.is_terminal

    vector_hits = result.get("vector_hits", 0)
    graph_expanded = result.get("graph_expanded", 0)

    if is_tty:
        _format_rich(console, query, vector_hits, graph_expanded, result)
    else:
        _format_plain(query, vector_hits, graph_expanded, result)


def _format_rich(
    console: Console,
    query: str,
    vector_hits: int,
    graph_expanded: int,
    result: dict[str, Any],
) -> None:
    console.print(f"[bold]Query:[/] {query}")
    console.print(
        f"[bold]Hits:[/] {vector_hits} vector, {graph_expanded} graph-expanded"
    )

    for i, hit in enumerate(result.get("results", []), start=1):
        console.rule(f"Result {i}", style="dim")

        file_label = _build_file_label(hit)
        console.print(f"  [cyan]File:[/] {file_label}")

        provenance_label = _build_provenance_label(hit)
        console.print(f"  [green]Provenance:[/] {provenance_label}")

        slug = hit.get("slug")
        if slug:
            console.print(f"  [yellow]Slug:[/] {slug}")

        graph_path = hit.get("graph_path")
        if graph_path:
            console.print(f"  [magenta]Path:[/] {graph_path}")

        content = hit.get("content", "")
        if content:
            display = _truncate_content(content)
            console.print(Text(display))


def _format_plain(
    query: str,
    vector_hits: int,
    graph_expanded: int,
    result: dict[str, Any],
) -> None:
    import click

    click.echo(f"Query: {query}")
    click.echo(f"Hits: {vector_hits} vector, {graph_expanded} graph-expanded")

    for i, hit in enumerate(result.get("results", []), start=1):
        click.echo(f"── Result {i} ──────────────────────────────")

        file_label = _build_file_label(hit)
        click.echo(f"  File: {file_label}")

        provenance_label = _build_provenance_label(hit)
        click.echo(f"  Provenance: {provenance_label}")

        slug = hit.get("slug")
        if slug:
            click.echo(f"  Slug: {slug}")

        graph_path = hit.get("graph_path")
        if graph_path:
            click.echo(f"  Path: {graph_path}")

        content = hit.get("content", "")
        if content:
            display = _truncate_content(content)
            click.echo(display)
