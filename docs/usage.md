# Using CodeKnow

The CLI talks to a CodeKnow API server. How it connects — and whether it manages the server's lifecycle — is controlled by a **mode** stored in `~/.codeknow/config.jsonl`. There are no endpoint environment variables; the config file is the single source of truth.

There are three modes:

- **`docker` (default)** — the CLI connects to the API exposed by the Docker Compose stack at `http://localhost:8080`, and `server start`/`stop`/`status` drive that stack (`docker compose up`/`down`/`ps`).
- **`remote`** — the CLI connects to an API it did not start, at a `remote_url` you set in the config file. Nothing to start or stop locally.
- **`daemon`** — the CLI spawns and manages a local `codeknow-api` background process on your machine, tracked via a PID file.

Every command below (`add`, `remove`, `search`, `info`, `clean`) works identically in all three modes. The only difference is *where the API server lives* and *who manages its lifecycle*.

---

## Endpoint resolution

The CLI resolves its API endpoint from the `mode` field in `~/.codeknow/config.jsonl`:

1. `mode: "docker"` (the default if the file is missing or malformed) → connect to `http://localhost:8080` as a remote API.
2. `mode: "remote"` → connect to `remote_url` as a remote API. Requires `remote_url` to be set in the config file.
3. `mode: "daemon"` → spawn and manage a local `codeknow-api` process bound on `host`:`port`.

The config file is a single-line JSON object:

```json
{"mode":"docker","remote_url":"","host":"localhost","port":8080}
```

Switch modes with the `server` command group (see below). There are no `CODEKNOW_*` endpoint environment variables.

---

## The `server` command group

```bash
codeknow server mode              # print the current mode
codeknow server mode docker       # set the mode (docker | remote | daemon)
codeknow server start             # start the server for the current mode
codeknow server stop              # stop the server
codeknow server status            # show server status
```

What `start` / `stop` / `status` do depends on the current mode:

| Mode | `start` | `stop` | `status` |
|---|---|---|---|
| **docker** (default) | `docker compose -f infra/docker-compose.yml up -d` (run from the repository root) | `... down` | `... ps` |
| **remote** | Prints "nothing to start" (CLI does not manage a remote server) | Prints "nothing to stop" | Pings `remote_url` and reports reachable/unreachable |
| **daemon** | Spawns the local `codeknow-api` process, prints its PID | Stops the process via its PID file | Reports running/not-running + PID |

Notes:

- **docker** requires `docker` on `PATH` and `infra/docker-compose.yml` present (i.e. run from the repo root). The CLI connects to the API at `http://localhost:8080`.
- **remote** requires `remote_url` to be set in `~/.codeknow/config.jsonl`. If it's missing, API commands raise a clear error. Switch back any time with `codeknow server mode docker`. (There is no CLI subcommand to edit `remote_url` yet — edit the config file directly.)
- **daemon** requires `codeknow-api` on `PATH` (i.e. `uv sync` in the workspace). The CLI binds on `host`:`port`.

---

## Commands at a glance

| Command | What it does |
|---|---|
| `codeknow add <ssh-url>` | Index a GitHub repo by its SSH URL |
| `codeknow remove <slug>` | Remove an indexed repo by slug |
| `codeknow search "<query>"` | Search the code index (use `--slug` to filter, repeatable) |
| `codeknow info` | Show API endpoint status and indexed repo slugs |
| `codeknow clean` | Remove cached repos, graph output, and temp files (`-y` skips confirm) |
| `codeknow server <subcommand>` | Manage the API server (`mode` / `start` / `stop` / `status`) |

---

## docker mode (default)

The CLI connects to the API exposed by the Docker Compose stack at `localhost:8080` — which is exactly where it points by default — so **no configuration is needed**:

```bash
# 1. Bring up the full stack (API + ChromaDB + Redis + model) from the repo root
codeknow server start

# 2. Index a repo
codeknow add git@github.com:owner/repo.git

# 3. Search across all indexed repos
codeknow search "how does auth work"

# 4. See endpoint status and available slugs
codeknow info        # API: http://localhost:8080 (remote)

# 5. Stop the stack
codeknow server stop
```

`server start`/`stop` are wrappers around `docker compose -f infra/docker-compose.yml up -d` / `down`, so they must be run from the repository root.

---

## remote mode

Point the CLI at a shared, remote, or cloud-hosted API. First set the mode and put the URL in the config file:

```bash
codeknow server mode remote
# edit ~/.codeknow/config.jsonl and set "remote_url": "https://api.example.com"
```

Then use it:

```bash
codeknow add git@github.com:owner/repo.git
codeknow info        # API: https://api.example.com (remote)
```

How remote mode differs:

- **No local server is spawned or managed.** `server start` / `server stop` print "nothing to start" / "nothing to stop".
- **`codeknow info`** reports the API URL rather than a local PID.
- **`codeknow clean`** still clears local on-disk caches, but it will not try to stop a local server.
- `codeknow-api` does **not** need to be on your `$PATH` in remote mode.

Switch back any time:

```bash
codeknow server mode docker
```

---

## daemon mode

Set the mode to `daemon` to have the CLI launch and manage a `codeknow-api` background process:

```bash
codeknow server mode daemon
```

The daemon is tracked via a PID file at `/tmp/codeknow-daemon.pid`. By default it binds on `localhost:8080`. Configure the bind address via the `host` / `port` fields in `~/.codeknow/config.jsonl`.

```bash
codeknow server start      # launch the API server in the background
codeknow server status     # is it running? (reports PID)
codeknow server stop       # stop the server
```

### Walkthrough

```bash
# 1. Switch to daemon mode
codeknow server mode daemon

# 2. Start the background server
codeknow server start

# 3. Index a repo (output shows the generated slug, node count, and edge count)
codeknow add git@github.com:owner/repo.git

# 4. Search
codeknow search "how does auth work"

# 5. Stop the server when you're done
codeknow server stop
```

---

## Configuration

The CLI reads a single-line JSON object from `~/.codeknow/config.jsonl`:

```json
{"mode":"docker","remote_url":"","host":"localhost","port":8080}
```

| Field | Used in | Default | Description |
|---|---|---|---|
| `mode` | all modes | `docker` | One of `docker`, `remote`, `daemon`. Defaults to `docker` if the file is missing or malformed. |
| `remote_url` | `remote` | `""` | The API base URL. Required in remote mode; ignored otherwise. |
| `host` | `daemon` | `localhost` | Bind host for the local `codeknow-api` process. |
| `port` | `daemon` | `8080` | Bind port for the local `codeknow-api` process. |

Change the mode with `codeknow server mode <mode>`. Edit the file directly to set `remote_url` / `host` / `port`.

Example — run the local daemon on a non-default port (edit the config file, then start):

```json
{"mode":"daemon","remote_url":"","host":"localhost","port":8181}
```

```bash
codeknow server start
```

---

## Running `codeknow-api` directly

The daemon is just `codeknow-api` launched in the background. You can also run it yourself — useful for development or when you want explicit control.

```bash
codeknow-api                 # production mode, binds 127.0.0.1:8080
codeknow-api --debug         # debug mode (uvicorn auto-reload + debug logging)
```

| Flag | Default | Env var | Description |
|---|---|---|---|
| `--host` | `127.0.0.1` | `CODEKNOW_API_HOST` | Bind host |
| `--port` | `8080` | `CODEKNOW_API_PORT` | Bind port |
| `--debug` | off | — | Enable auto-reload + debug logging |

Point the CLI at a server you started manually by switching to remote mode and setting `remote_url`:

```bash
# Start a server on a custom port...
codeknow-api --port 8181 &

# ...and point the CLI at it
codeknow server mode remote
# edit ~/.codeknow/config.jsonl: {"mode":"remote","remote_url":"http://localhost:8181",...}
codeknow info
```

---

## Running the API via Docker Compose

The easiest way to get a full stack running — no `uv` or Python needed on the host. A single Compose file brings up ChromaDB, Redis, the embedding model (via Docker Model Runner), **and** the `codeknow-api` server:

```bash
# from the repository root
codeknow server start     # runs: docker compose -f infra/docker-compose.yml up -d --build
```

The API is published on `localhost:8080`, which is exactly where the CLI points by default — so **no configuration is needed**:

```bash
codeknow info        # API: http://localhost:8080 (remote)
codeknow add git@github.com:owner/repo.git
```

Notes:

- **Networking inside the stack:** the `api` container reaches ChromaDB/Redis by compose hostname (`chromadb:8000`, `redis:6379`) and reaches the host's Docker Model Runner via `host.docker.internal:12434`. The CLI talks to the API on the host-published port only.
- **Persistent data:** generated graphs and cloned-repo temp space live in `infra/api-data/` (mounted at `/data`).
- **Prerequisites:** Docker with the Compose plugin and Docker Model Runner enabled. See [infra-setup.md](infra-setup.md) for the full setup.

```bash
codeknow server status     # check status (runs: docker compose ps)
codeknow server stop       # stop the stack (runs: docker compose down)
```

---

## Troubleshooting

### "Cannot connect to the API at \<url\>"

The CLI can't reach its API endpoint. Check which mode you're in (`codeknow server mode`) and that the corresponding server is reachable:

```bash
codeknow server mode      # print the current mode
codeknow server status    # check the server for the current mode
```

In **docker** mode, verify the stack is up:

```bash
docker compose -f infra/docker-compose.yml ps
```

In **remote** mode, check that `remote_url` in `~/.codeknow/config.jsonl` is correct, includes the scheme (`http://` or `https://`), and that the server is actually running and reachable from your machine.

### "Cannot connect to the daemon. Start it with: codeknow server start"

You're in **daemon mode** but the server isn't running. Start it:

```bash
codeknow server start
```

If it just started, give it a moment — the CLI waits for the server to become ready, but a heavily loaded machine can be slow.

### Port already in use

The default port is `8080`. If something else is using it, pick another. In daemon mode, edit `port` in `~/.codeknow/config.jsonl`:

```json
{"mode":"daemon","remote_url":"","host":"localhost","port":8181}
```

```bash
codeknow server start
```

### Server won't stop

Force-kill the tracked process directly if `codeknow server stop` doesn't return cleanly:

```bash
codeknow server stop
# if needed, remove the PID file at /tmp/codeknow-daemon.pid and kill the process manually
```

### Checking which mode you're in

```bash
codeknow server mode       # print the active mode
codeknow info              # also shows the resolved endpoint
```

- `API: <url> (remote)` → docker or remote mode (the CLI treats the endpoint as a remote API it doesn't manage). If the URL is `http://localhost:8080` it's the local Docker stack.
- `Daemon: running (PID …)` → daemon mode.
