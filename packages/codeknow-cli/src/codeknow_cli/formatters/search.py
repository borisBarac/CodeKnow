from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

if TYPE_CHECKING:
    from codeknow_cli.client import SearchHit, SearchResult

_CONTENT_TRUNCATE = 200


def _build_file_label(hit: SearchHit) -> str:
    file_loc = hit.file
    if hit.start_line is not None and hit.end_line is not None:
        return f"{file_loc}:{hit.start_line}-{hit.end_line}"
    return str(file_loc)


def _build_provenance_label(hit: SearchHit) -> str:
    provenance = hit.provenance
    if hit.distance is not None:
        return f"{provenance} (distance: {hit.distance})"
    if hit.weight is not None:
        return f"{provenance} (weight: {hit.weight})"
    return str(provenance)


def _truncate_content(content: str) -> str:
    if len(content) > _CONTENT_TRUNCATE:
        return content[:_CONTENT_TRUNCATE] + "..."
    return content


def format_search_results(query: str, result: SearchResult) -> None:
    console = Console(force_terminal=None)
    is_tty = console.is_terminal

    if is_tty:
        _format_rich(console, query, result)
    else:
        _format_plain(query, result)


def _format_rich(
    console: Console,
    query: str,
    result: SearchResult,
) -> None:
    console.print(f"[bold]Query:[/] {query}")
    console.print(
        f"[bold]Hits:[/] {result.vector_hits} vector, "
        f"{result.graph_expanded} graph-expanded"
    )

    for i, hit in enumerate(result.results, start=1):
        console.rule(f"Result {i}", style="dim")

        file_label = _build_file_label(hit)
        console.print(f"  [cyan]File:[/] {file_label}")

        provenance_label = _build_provenance_label(hit)
        console.print(f"  [green]Provenance:[/] {provenance_label}")

        if hit.slug:
            console.print(f"  [yellow]Slug:[/] {hit.slug}")

        if hit.graph_path:
            console.print(f"  [magenta]Path:[/] {hit.graph_path}")

        if hit.content:
            display = _truncate_content(hit.content)
            console.print(Text(display))


def _format_plain(
    query: str,
    result: SearchResult,
) -> None:
    import click

    click.echo(f"Query: {query}")
    click.echo(
        f"Hits: {result.vector_hits} vector, {result.graph_expanded} graph-expanded"
    )

    for i, hit in enumerate(result.results, start=1):
        click.echo(f"── Result {i} ──────────────────────────────")

        file_label = _build_file_label(hit)
        click.echo(f"  File: {file_label}")

        provenance_label = _build_provenance_label(hit)
        click.echo(f"  Provenance: {provenance_label}")

        if hit.slug:
            click.echo(f"  Slug: {hit.slug}")

        if hit.graph_path:
            click.echo(f"  Path: {hit.graph_path}")

        if hit.content:
            display = _truncate_content(hit.content)
            click.echo(display)
