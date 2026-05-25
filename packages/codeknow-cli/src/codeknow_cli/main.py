"""codeknow CLI — command-line interface powered by Click."""

from __future__ import annotations

import sys

import click
from daemonocle.cli import DaemonCLI

from codeknow_cli import __version__
from codeknow_cli.client import DEFAULT_PID_FILE, Client
from codeknow_cli.daemon import run_server
from codeknow_cli.exceptions import (
    ApiError,
    CodeknowError,
    ConfigError,
    DaemonAlreadyRunningError,
    DaemonNotRunningError,
    DaemonTimeoutError,
    RepoConflictError,
    RepoNotFoundError,
    ValidationError,
)


@click.group()
@click.version_option(version=__version__, prog_name="codeknow")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Codeknow — code knowledge graph toolkit."""
    ctx.ensure_object(dict)
    ctx.obj["client"] = Client()


@cli.command()
@click.argument("ssh_url")
@click.pass_context
def add(ctx: click.Context, ssh_url: str) -> None:
    """Add a GitHub repo to the index (by SSH URL)."""
    client: Client = ctx.obj["client"]
    result = client.add_to_index(ssh_url)
    click.echo(f"Status: {result.status}")
    if result.slug:
        click.echo(f"Slug:   {result.slug}")
    if result.node_count is not None:
        click.echo(f"Nodes:  {result.node_count}")
        click.echo(f"Edges:  {result.edge_count}")


@cli.command()
@click.argument("slug")
@click.pass_context
def remove(ctx: click.Context, slug: str) -> None:
    """Remove a repo from the index (by slug)."""
    client: Client = ctx.obj["client"]
    result = client.remove_from_index(slug)
    click.echo(f"Status: {result.get('status')}")
    if result.get("slug"):
        click.echo(f"Slug:   {result['slug']}")
    if result.get("chunks_deleted") is not None:
        click.echo(f"Chunks deleted: {result['chunks_deleted']}")


@cli.command()
@click.argument("query")
@click.option(
    "--slug", "slugs", multiple=True, help="Filter to specific repo slugs (repeatable)."
)
@click.pass_context
def search(ctx: click.Context, query: str, slugs: tuple[str, ...]) -> None:
    """Search the code index."""
    client: Client = ctx.obj["client"]
    slug_list = list(slugs) if slugs else None
    result = client.search(query, slugs=slug_list)

    click.echo(f"Query: {query}")
    vector_hits = result.get("vector_hits", 0)
    graph_expanded = result.get("graph_expanded", 0)
    click.echo(f"Hits: {vector_hits} vector, {graph_expanded} graph-expanded")

    for i, hit in enumerate(result.get("results", []), start=1):
        click.echo(f"── Result {i} ──────────────────────────────")
        file_loc = hit.get("file", "?")
        start_line = hit.get("start_line")
        end_line = hit.get("end_line")
        if start_line is not None and end_line is not None:
            click.echo(f"  File: {file_loc}:{start_line}-{end_line}")
        else:
            click.echo(f"  File: {file_loc}")
        provenance = hit.get("provenance", "unknown")
        distance = hit.get("distance")
        weight = hit.get("weight")
        if distance is not None:
            click.echo(f"  Provenance: {provenance} (distance: {distance})")
        elif weight is not None:
            click.echo(f"  Provenance: {provenance} (weight: {weight})")
        else:
            click.echo(f"  Provenance: {provenance}")
        slug = hit.get("slug")
        if slug:
            click.echo(f"  Slug: {slug}")
        graph_path = hit.get("graph_path")
        if graph_path:
            click.echo(f"  Path: {graph_path}")
        content = hit.get("content", "")
        if content:
            display = content[:200] + "..." if len(content) > 200 else content
            click.echo(display)


@cli.command(
    cls=DaemonCLI,
    daemon_params={"pid_file": DEFAULT_PID_FILE},
)
def daemon() -> None:
    """Manage the codeknow background service."""
    run_server()


def main() -> None:
    """Entry point for the ``codeknow`` console script."""
    try:
        cli()
    except DaemonNotRunningError:
        click.echo(
            "Error: Cannot connect to the daemon. "
            "Start it with: codeknow daemon start",
            err=True,
        )
        sys.exit(1)
    except DaemonTimeoutError:
        click.echo(
            "Error: Daemon is not responding. "
            "It may still be starting up — try again in a moment.",
            err=True,
        )
        sys.exit(1)
    except DaemonAlreadyRunningError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except ConfigError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except RepoNotFoundError as exc:
        click.echo(f"Repo not found: {exc}", err=True)
        sys.exit(1)
    except RepoConflictError as exc:
        click.echo(f"Conflict: {exc}", err=True)
        sys.exit(1)
    except ValidationError as exc:
        click.echo(f"Invalid input: {exc}", err=True)
        sys.exit(1)
    except ApiError as exc:
        click.echo(f"API error: {exc}", err=True)
        sys.exit(1)
    except CodeknowError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
