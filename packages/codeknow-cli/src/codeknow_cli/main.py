"""codeknow CLI — command-line interface powered by Click."""

from __future__ import annotations

import sys

import click
from daemonocle.cli import DaemonCLI

from codeknow_cli import __version__
from codeknow_cli.client import DEFAULT_PID_FILE, Client, ClientError
from codeknow_cli.daemon import run_server


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
    except ClientError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
