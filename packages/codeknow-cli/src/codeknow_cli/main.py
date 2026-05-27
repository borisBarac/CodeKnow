"""codeknow CLI — command-line interface powered by Click."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import click
from code_know_api_client.types import Unset
from daemonocle.cli import DaemonCLI

from codeknow_cli import __version__
from codeknow_cli.client import Client
from codeknow_cli.daemon import run_server
from codeknow_cli.endpoint import DEFAULT_PID_FILE
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
from codeknow_cli.formatters import format_search_results


def _env_path(key: str, default: Path) -> Path:
    raw = os.environ.get(key)
    return Path(raw) if raw else default


def _dir_size(path: Path) -> str:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    if total < 1024:
        return f"{total} B"
    if total < 1024 * 1024:
        return f"{total / 1024:.1f} KB"
    if total < 1024 * 1024 * 1024:
        return f"{total / (1024 * 1024):.1f} MB"
    return f"{total / (1024 * 1024 * 1024):.1f} GB"


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
    click.echo(f"Status: {result.status}")
    if result.slug:
        click.echo(f"Slug:   {result.slug}")
    click.echo(f"Chunks deleted: {result.chunks_deleted}")


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
    format_search_results(query, result)


@cli.command()
@click.pass_context
def info(ctx: click.Context) -> None:
    """Show daemon status and available repo slugs."""
    client: Client = ctx.obj["client"]

    if client.is_remote:
        click.echo(f"API: {client.base_url} (remote)")
    else:
        running = client.check_daemon()
        if not running:
            click.echo("Daemon: not running")
            return

        pid = client.get_daemon_pid()
        pid_str = f" (PID {pid})" if pid else ""
        click.echo(f"Daemon: running{pid_str}")

    try:
        repos_resp = client.list_repos()
        repos = repos_resp.repos
    except (ApiError, DaemonNotRunningError, ValidationError):
        click.echo("Repos: unavailable (could not reach daemon)")
        return

    if not repos:
        click.echo("Repos: (none)")
        return

    click.echo(f"Repos ({len(repos)}):")
    for repo in repos:
        parts = [repo.slug]
        if not isinstance(repo.build_status, Unset) and repo.build_status:
            parts.append(f"build={repo.build_status}")
        if not isinstance(repo.health, Unset) and repo.health:
            parts.append(f"health={repo.health}")
        click.echo(f"  {'  '.join(parts)}")


@cli.command()
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def clean(ctx: click.Context, yes: bool) -> None:
    """Remove cached repos, graph output, and temp files."""
    from codeknow.pipeline.config import _CODEKNOW_HOME

    client: Client = ctx.obj["client"]

    if not client.is_remote and client.check_daemon():
        click.echo("Stopping daemon...")
        client.stop_daemon()
        click.echo("Daemon stopped.")

    default_input = _env_path("CODEKNOW_INPUT_DIR", _CODEKNOW_HOME / "repos")
    default_output = _env_path("CODEKNOW_OUTPUT_DIR", _CODEKNOW_HOME / "graph")
    default_temp = _env_path("CODEKNOW_TEMP_DIR", _CODEKNOW_HOME / "temp")
    targets = [
        ("repos cache", default_input),
        ("graph output", default_output),
        ("temp files", default_temp),
    ]

    for label, path in targets:
        if not path.exists():
            click.echo(f"{label}: {path} does not exist, skipping.")
            continue

        size = _dir_size(path)
        if not yes:
            if not click.confirm(f"Remove {label} at {path} ({size})?"):
                click.echo(f"Skipped {label}.")
                continue

        shutil.rmtree(path)
        click.echo(f"Removed {label}: {path} ({size})")


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
    except DaemonNotRunningError as exc:
        click.echo(f"Error: {exc}", err=True)
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
