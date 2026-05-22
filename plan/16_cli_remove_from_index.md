# Plan 16: `remove_from_index(slug: str) -> dict`

[implemented]

## API Contract

`DELETE /v1/repos` with `{"url": "<ssh_url>"}`, returns `200 OK`. Note: the API expects `url` (SSH URL), **not** slug.

## Design Decision

The method signature takes `slug` but the API expects `url`.

**Chosen approach (Option A):** Resolve slug → URL by calling `GET /v1/repos` first, finding the matching repo, and extracting its `github_ssh_url`. Then call DELETE with that URL. This keeps the CLI user-friendly (users reference repos by slug).

## Client Method Changes (`client.py`)

1. Call `GET /v1/repos` to list all repos
2. Find the repo with matching `slug` (iterate `repos` list)
3. If not found: raise `ValueError(f"Repo with slug '{slug}' not found")`
4. Send `httpx.request("DELETE", f"{self.base_url}/v1/repos", json={"url": repo.github_ssh_url}, timeout=30.0)`
5. On `200`: return `resp.json()` (contains `status`, `slug`, `chunks_deleted`)
6. On `404`: raise error ("Repo not found")

## CLI Command (`main.py`)

- Add `@cli.command("remove")` with argument `SLUG`
- Optionally ensure daemon is running first
- Print confirmation with slug and chunks_deleted count

## Fake Server (`fake_server.py`)

- Already handles `DELETE /v1/repos` → 200 (no changes needed)

## Tests

- Unit test: mock both GET and DELETE calls, verify slug resolution
- Test with non-existent slug raises ValueError
- Integration test: against fake server
