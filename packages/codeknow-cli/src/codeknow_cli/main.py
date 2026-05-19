"""codeknow CLI — command-line interface powered by Click."""

from __future__ import annotations

import click
from daemonocle.cli import DaemonCLI

from codeknow_cli import __version__
from codeknow_cli.client import Client
from codeknow_cli.daemon import run_server


@click.group()
@click.version_option(version=__version__, prog_name="codeknow")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Codeknow — code knowledge graph toolkit."""
    ctx.ensure_object(dict)
    ctx.obj["client"] = Client()


@cli.command(
    cls=DaemonCLI,
    daemon_params={"pid_file": "/tmp/codeknow-daemon.pid"},  # noqa: S108
)
def daemon() -> None:
    """Manage the codeknow background service."""
    run_server()


def main() -> None:
    """Entry point for the ``codeknow`` console script."""
    cli()
