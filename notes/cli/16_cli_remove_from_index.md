# Plan 16: `remove_from_index` — Implementation Notes

## What was done

Implemented `remove_from_index(slug: str) -> dict` using the generated API client,
following the same pattern as Plan 14 (`add_to_index`). Added CLI `remove` command,
updated the fake server, and added integration + unit tests.

## Implementation: two-phase slug resolution

The API's `DELETE /v1/repos` expects `{"url": "<ssh_url>"}`, but the CLI takes a
slug. So the method:

1. Calls `list_repos_v1_repos_get.sync_detailed()` to fetch all repos
2. Finds the repo matching `slug`
3. Calls `delete_repo_v1_repos_delete.sync_detailed(body=DeleteRepoRequest(url=repo.github_ssh_url))`

All errors are wrapped as `ClientError` — same pattern as `add_to_index`.

## Delete response model is generic

`DeleteRepoV1ReposDeleteResponseDeleteRepoV1ReposDelete` is an empty attrs class.
All data lives in `additional_properties` (a `dict[str, Any]`). The method returns
`dict(del_resp.parsed.additional_properties)` — gives back `{"status": "deleted",
"slug": "...", "chunks_deleted": N}`.

## Import alias for long model name

```python
from code_know_api_client.models import (
    delete_repo_v1_repos_delete_response_delete_repo_v1_repos_delete as _del_resp,
)
```

Used `isinstance(resp.parsed, _del_resp.DeleteRepoV1ReposDeleteResponseDeleteRepoV1ReposDelete)`
for type narrowing. The alias keeps imports under 88 chars.

## Fake server update

`GET /v1/repos` now returns a stub repo instead of an empty list. The stub is a
module-level dict with `ClassVar` annotation on the handler class:

```python
_STUB_REPO = {
    "github_ssh_url": "git@github.com:stub/repo.git",
    "slug": "stub-slug",
    ...
}

class StubAPIHandler(BaseHTTPRequestHandler):
    STUB_REPO: ClassVar[dict] = _STUB_REPO
```

This allows slug resolution to work in integration tests.

## CLI command

```python
@cli.command()
@click.argument("slug")
@click.pass_context
def remove(ctx: click.Context, slug: str) -> None:
    client: Client = ctx.obj["client"]
    result = client.remove_from_index(slug)
    click.echo(f"Status: {result.get('status')}")
    if result.get("slug"):
        click.echo(f"Slug:   {result['slug']}")
    if result.get("chunks_deleted") is not None:
        click.echo(f"Chunks deleted: {result['chunks_deleted']}")
```

Uses `.get()` on the dict result (unlike `add` which accesses attrs directly).

## Tests

- **Integration** (`test_remove_from_index_success`): starts fake server, calls
  `remove_from_index("stub-slug")`, asserts response dict
- **Integration** (`test_remove_from_index_slug_not_found`): starts fake server,
  calls with non-existent slug, asserts `ClientError`
- **Unit** (`test_remove_resolves_slug_to_ssh_url`): mocks both generated client
  modules, verifies the correct `ssh_url` is passed to `DeleteRepoRequest`
- **Unit** (`test_remove_raises_when_slug_not_found`): mocks list repos to return
  empty, asserts `ClientError`

Mock setup uses real model instances (`ListReposResponse`, `RepoMetadata`,
`DeleteRepoV1ReposDeleteResponseDeleteRepoV1ReposDelete`) wrapped in `ApiResponse`
so `isinstance` checks in production code work correctly.

## Known test gaps (from review)

- `test_fake_server.py::test_get_repos_returns_empty_list` is stale — fake server
  now returns a stub repo, so `repos == []` assertion will fail
- No tests for `add_to_index` (4 branches: 202, 409, 422, unexpected)
- No `remove` command tests in `test_cli.py`
- No test for `main()` error handler (`ClientError` → stderr + exit 1)
- 7 error-path branches in `remove_from_index` untested (UnexpectedStatus from
  list, 422 from list, unexpected from list, 404 from delete, unexpected from
  delete, 422 from delete, unexpected from delete)
- `_free_port()`, `_started_pids`, `_atexit_cleanup()` duplicated across
  `test_client.py` and `test_daemon_manager.py` — should move to `conftest.py`

## Files changed

- `packages/codeknow-cli/src/codeknow_cli/client.py` — implemented `remove_from_index`
- `packages/codeknow-cli/src/codeknow_cli/main.py` — added `remove` command
- `packages/codeknow-cli/src/codeknow_cli/daemon/fake_server.py` — stub repo in GET
- `packages/codeknow-cli/tests/daemon/test_client.py` — 4 new tests
