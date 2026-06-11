# Plan 15: `search(query: str, slug: str | None = None) -> dict`

## API Contract

`POST /v1/search` with `{"query": "...", "top_k": 10, "repos": ["slug"] | null}`, returns `200 OK` with `SearchResponse`.

## Client Method Changes (`client.py`)

1. Build request body: `{"query": query, "top_k": 10}` + conditionally add `"repos": [slug]` if `slug` is not None
2. Send `httpx.post(f"{self.base_url}/v1/search", json=body, timeout=30.0)`
3. On `200`: return `resp.json()` (contains `query`, `vector_hits`, `graph_expanded`, `results`)
4. On `400`: raise error with "Unknown slugs" detail
5. On `409`: raise error with "Repos being rebuilt" detail
6. On `422`: raise validation error

## CLI Command (`main.py`)

- Add `@cli.command("search")` with argument `QUERY` and option `--slug` (optional, repeatable)
- Optionally ensure daemon is running first
- Print results in a formatted table/list

## Fake Server (`fake_server.py`)

- Already handles `POST /v1/search` → 200 (no changes needed)

## Tests

- Unit test: mock httpx, verify body includes `repos` only when slug is provided
- Integration test: against fake server
