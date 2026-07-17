"""codeknow CLI — command-line interface powered by Click."""

from __future__ import annotations

import functools
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
from code_know_api_client.types import Unset
from codeknow.pipeline.config import _CODEKNOW_HOME, _env_path

from codeknow_cli import __version__
from codeknow_cli.client import Client
from codeknow_cli.config import VALID_MODES, load_config, save_config
from codeknow_cli.exceptions import (
    ApiError,
    CodeknowError,
    DaemonNotRunningError,
    DaemonTimeoutError,
    RepoConflictError,
    RepoNotFoundError,
    ValidationError,
)
from codeknow_cli.formatters import format_search_results
from codeknow_cli.server import get_backend

if TYPE_CHECKING:
    from collections.abc import Callable

    from codeknow_cli.client import BuildStatusResult


def _dir_size(path: Path) -> str:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    if total < 1024:
        return f"{total} B"
    if total < 1024 * 1024:
        return f"{total / 1024:.1f} KB"
    if total < 1024 * 1024 * 1024:
        return f"{total / (1024 * 1024):.1f} MB"
    return f"{total / (1024 * 1024 * 1024):.1f} GB"


def requires_server(fn: Callable[..., object]) -> Callable[..., object]:
    """Pre-flight check: refuse to run if the API server is not reachable."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        if not Client().check_server():
            click.echo(
                "Server is not running. Start it with: codeknow server start",
                err=True,
            )
            raise SystemExit(1)
        return fn(*args, **kwargs)

    return wrapper


@click.group()
@click.version_option(version=__version__, prog_name="codeknow")
def cli() -> None:
    """Codeknow — code knowledge graph toolkit."""


def _show_build_result(result: BuildStatusResult) -> None:
    click.echo()
    click.echo(f"Status: {result.status}")
    if result.slug:
        click.echo(f"Slug:   {result.slug}")
    if result.commit_hash:
        click.echo(f"Commit: {result.commit_hash}")
    if result.node_count is not None:
        click.echo(f"Nodes:  {result.node_count}")
    if result.edge_count is not None:
        click.echo(f"Edges:  {result.edge_count}")
    if result.community_count is not None:
        click.echo(f"Communities: {result.community_count}")


def _run_build(client: Client, ssh_url: str, *, force: bool, fetch: bool) -> None:
    def _on_progress(stage: str, percent: int, message: str) -> None:
        label = stage or "building"
        parts = f"[{label}] {percent}%"
        if message:
            parts = f"{parts} — {message}"
        click.echo(f"\r\033[K{parts}", nl=False)

    result = client.add_to_index(
        ssh_url,
        progress_callback=_on_progress,
        force_rebuild=force,
        fetch_remote=fetch,
    )
    _show_build_result(result)


def _repo_url(client: Client, slug: str) -> str:
    for repo in client.list_repos().repos:
        if repo.slug == slug:
            return repo.github_ssh_url
    msg = f"Repo with slug '{slug}' not found"
    raise RepoNotFoundError(msg)


@cli.command()
@click.argument("ssh_url")
@requires_server
def add(ssh_url: str) -> None:
    """Add a GitHub repo to the index (by SSH URL)."""
    _run_build(Client(), ssh_url, force=False, fetch=True)


def _rebuild_slug(slug: str, *, force: bool, fetch: bool) -> None:
    client = Client()
    _run_build(client, _repo_url(client, slug), force=force, fetch=fetch)


@cli.command()
@click.argument("slug")
@click.option("--no-fetch", is_flag=True, help="Build the cached checkout as-is.")
@requires_server
def reindex(slug: str, no_fetch: bool) -> None:
    """Incrementally reindex an existing repo."""
    _rebuild_slug(slug, force=False, fetch=not no_fetch)


@cli.command()
@click.argument("slug")
@click.option("--no-fetch", is_flag=True, help="Build the cached checkout as-is.")
@requires_server
def rebuild(slug: str, no_fetch: bool) -> None:
    """Fully rebuild an existing repo and publish it atomically."""
    _rebuild_slug(slug, force=True, fetch=not no_fetch)


@cli.command()
@click.argument("slug")
@requires_server
def remove(slug: str) -> None:
    """Remove a repo from the index (by slug)."""
    client = Client()
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
@click.option("--full", is_flag=True, help="Print full chunk content.")
@requires_server
def search(query: str, slugs: tuple[str, ...], full: bool) -> None:
    """Search the code index."""
    client = Client()
    slug_list = list(slugs) if slugs else None
    result = client.search(query, slugs=slug_list)
    format_search_results(query, result, full=full)


@cli.command()
@click.option(
    "--slug", "slugs", multiple=True, help="Restrict search to slugs (repeatable)."
)
def repl(slugs: tuple[str, ...]) -> None:
    """Interactive REPL agent for testing search (dev only)."""
    from codeknow_cli.repl_agent import run_repl

    run_repl(slugs=slugs)


@cli.command()
def info() -> None:
    """Show API endpoint status and available repo slugs."""
    client = Client()

    if client.is_remote:
        click.echo(f"API: {client.base_url} ({client.mode})")
    else:
        running = client.check_server()
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
def clean(yes: bool) -> None:
    """Remove cached repos, graph output, and temp files."""
    client = Client()

    if client.mode in {"docker", "daemon"} and client.check_server():
        try:
            repos = client.list_repos().repos
        except (ApiError, DaemonNotRunningError, ValidationError):
            repos = []

        for repo in repos:
            slug = repo.slug
            if not slug:
                continue
            if not yes and not click.confirm(f"Remove indexed repo {slug}?"):
                click.echo(f"Skipped indexed repo: {slug}")
                continue
            result = client.remove_from_index(slug)
            click.echo(f"Removed indexed repo: {result.slug}")

    if not client.is_remote and client.check_daemon():
        click.echo("Stopping daemon...")
        client.stop_daemon()
        click.echo("Daemon stopped.")

    default_input = _env_path("CODEKNOW_INPUT_DIR", _CODEKNOW_HOME / "repos")
    default_output = _env_path(
        "CODEKNOW_GRAPH_DIR",
        _env_path("CODEKNOW_OUTPUT_DIR", _CODEKNOW_HOME / "graph"),
    )
    default_temp = _env_path("CODEKNOW_TEMP_DIR", _CODEKNOW_HOME / "temp")
    targets = [
        ("repos cache", default_input),
        ("graph output", default_output),
        ("temp files", default_temp),
    ]
    if client.mode == "docker":
        targets.extend(
            [
                ("docker graph output", Path("infra/api-data/graph")),
                ("docker temp files", Path("infra/api-data/temp")),
            ]
        )

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


@cli.group()
def server() -> None:
    """Manage the codeknow server."""


@server.command()
@click.argument("mode_value", required=False)
def mode(mode_value: str | None) -> None:
    """Show or set the server mode (docker|remote|daemon)."""
    if mode_value is None:
        cfg = load_config()
        click.echo(f"Mode: {cfg.mode}")
        return
    if mode_value not in VALID_MODES:
        msg = f"invalid mode '{mode_value}'. Must be one of: docker, remote, daemon"
        raise click.BadParameter(msg)
    cfg = load_config()
    cfg.mode = mode_value
    save_config(cfg)
    click.echo(f"Mode set to: {mode_value}")


@server.command()
def start() -> None:
    """Start the server for the current mode."""
    get_backend().start()


@server.command()
def stop() -> None:
    """Stop the server for the current mode."""
    get_backend().stop()


@server.command()
def status() -> None:
    """Show server status for the current mode."""
    get_backend().status()


def main() -> None:
    """Entry point for the ``codeknow`` console script."""
    try:
        cli()
    except DaemonTimeoutError:
        click.echo(
            "Error: Daemon is not responding. "
            "It may still be starting up — try again in a moment.",
            err=True,
        )
        sys.exit(1)
    except CodeknowError as exc:
        click.echo(f"{_error_label(exc)}: {exc}", err=True)
        sys.exit(1)


def _error_label(exc: CodeknowError) -> str:
    if isinstance(exc, RepoNotFoundError):
        return "Repo not found"
    if isinstance(exc, RepoConflictError):
        return "Conflict"
    if isinstance(exc, ValidationError):
        return "Invalid input"
    if isinstance(exc, ApiError):
        return "API error"
    return "Error"


if __name__ == "__main__":
    main()
