# Using CodeKnow

CodeKnow has two ways to run:

- **Remote mode (default)** — the CLI connects to an already-running API server. By default it points at `http://localhost:8080` (the Docker Compose stack); set `CODEKNOW_API_URL` to target any other host.
- **Local daemon mode (opt-in)** — set `CODEKNOW_DAEMON=1` and the CLI starts and manages a `codeknow-api` background process on your machine.

Every command below works the same in both modes. The only difference is *where the API server lives* and *who manages its lifecycle*.

---

## Endpoint resolution

The CLI resolves its API endpoint in this priority order:

1. `CODEKNOW_API_URL` — connect (remote) to an explicit URL. Highest priority.
2. `CODEKNOW_DAEMON=1` — local daemon mode: the CLI manages the `codeknow-api` process lifecycle.
3. Default — connect (remote) to the Docker endpoint at `http://localhost:8080`.

In the default (remote) mode the CLI only talks to the API; it does not start or stop a server, so the `daemon` subcommand is **hidden** from `codeknow --help`. Set `CODEKNOW_DAEMON=1` to expose daemon management commands.

---

## Commands at a glance

| Command | What it does |
|---|---|
| `codeknow add <ssh-url>` | Index a GitHub repo by its SSH URL |
| `codeknow remove <slug>` | Remove an indexed repo by slug |
| `codeknow search "<query>"` | Search the code index (use `--slug` to filter, repeatable) |
| `codeknow info` | Show API endpoint status and indexed repo slugs |
| `codeknow clean` | Remove cached repos, graph output, and temp files (`-y` skips confirm) |
| `codeknow daemon <action>` | Manage the background service (**daemon mode only**, `CODEKNOW_DAEMON=1`) |

---

## Remote mode (default)

In remote mode the CLI connects to an API it didn't start — usually the [Docker Compose stack](#running-the-api-via-docker-compose) at `localhost:8080`, or any other `codeknow-api` instance.

### Walkthrough (Docker stack)

```bash
# 1. Bring up the full stack (API + ChromaDB + Redis + model)
docker compose -f infra/docker-compose.yml up -d --build

# 2. Index a repo (the CLI targets localhost:8080 by default)
codeknow add git@github.com:owner/repo.git

# 3. Search across all indexed repos
codeknow search "how does auth work"

# 4. See endpoint status and available slugs
codeknow info        # API: http://localhost:8080 (remote)

# 5. Stop the stack
docker compose -f infra/docker-compose.yml down
```

### Pointing at a different host

Set `CODEKNOW_API_URL` to target a shared, remote, or cloud-hosted API:

```bash
export CODEKNOW_API_URL=https://api.example.com
codeknow add git@github.com:owner/repo.git
codeknow info        # API: https://api.example.com (remote)
```

### How remote mode differs

- **No local daemon is spawned.** The `daemon` subcommand is hidden (and a no-op if invoked).
- **`codeknow info`** reports the API URL rather than a local PID.
- **`codeknow clean`** still clears local on-disk caches, but it will not try to stop a local daemon.
- `codeknow-api` does **not** need to be on your `$PATH` in remote mode.

To switch to local daemon mode, opt in explicitly:

```bash
export CODEKNOW_DAEMON=1
```

---

## Local daemon mode (opt-in)

Set `CODEKNOW_DAEMON=1` to have the CLI launch and manage a `codeknow-api` background process (tracked via a PID file at `/tmp/codeknow-daemon.pid`):

```bash
export CODEKNOW_DAEMON=1

codeknow daemon start     # launch the API server in the background
codeknow daemon status    # is it running?
codeknow daemon restart   # restart the service
codeknow daemon stop      # stop the service
```

By default the daemon runs `codeknow-api` on `localhost:8080`. With `CODEKNOW_DAEMON=1` set, the `daemon` subcommand appears in `codeknow --help`.

### Walkthrough

```bash
export CODEKNOW_DAEMON=1

# 1. Start the background daemon
codeknow daemon start

# 2. Index a repo (output shows the generated slug, node count, and edge count)
codeknow add git@github.com:owner/repo.git

# 3. Search
codeknow search "how does auth work"

# 4. Stop the daemon when you're done
codeknow daemon stop
```

### Daemon flags

These come from the underlying daemon runner:

| Flag | Command(s) | Effect |
|---|---|---|
| `--debug` | `start`, `restart` | Run in the foreground (don't detach) |
| `--force` | `stop`, `restart` | Send SIGKILL if SIGTERM doesn't stop it |
| `--timeout <s>` | `stop`, `restart` | Seconds to wait before forcing |

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CODEKNOW_API_URL` | *(unset)* | Explicit remote API URL; takes priority over everything else. |
| `CODEKNOW_DAEMON` | *(unset)* | Set to `1` to enable local daemon mode (CLI manages the API process). |
| `CODEKNOW_HOST` | `localhost` | API server host (daemon mode) |
| `CODEKNOW_API_PORT` | `8080` | API server port (daemon mode) |

### Precedence

1. `CODEKNOW_API_URL` — if set, remote mode is active and everything else is ignored.
2. `CODEKNOW_DAEMON=1` — local daemon mode.
3. Built-in default — remote to `http://localhost:8080`.

Example — run the local daemon on a non-default port (daemon mode):

```bash
export CODEKNOW_DAEMON=1
export CODEKNOW_API_PORT=8181
codeknow daemon start
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

Point the CLI at a server you started manually by setting `CODEKNOW_API_URL`:

```bash
# Start a server on a custom port...
codeknow-api --port 8181 &

# ...and point the CLI at it
export CODEKNOW_API_URL=http://localhost:8181
codeknow info
```

---

## Running the API via Docker Compose

The easiest way to get a full stack running — no `uv` or Python needed on the host. A single Compose file brings up ChromaDB, Redis, the embedding model (via Docker Model Runner), **and** the `codeknow-api` server:

```bash
docker compose -f infra/docker-compose.yml up -d --build
```

The API is published on `localhost:8080`, which is exactly where the CLI points by default — so **no environment variables are needed**:

```bash
codeknow info        # API: http://localhost:8080 (remote)
codeknow add git@github.com:owner/repo.git
```

Notes:

- **Networking inside the stack:** the `api` container reaches ChromaDB/Redis by compose hostname (`chromadb:8000`, `redis:6379`) and reaches the host's Docker Model Runner via `host.docker.internal:12434`. The CLI talks to the API on the host-published port only.
- **Persistent data:** generated graphs and cloned-repo temp space live in `infra/api-data/` (mounted at `/data`).
- **Prerequisites:** Docker with the Compose plugin and Docker Model Runner enabled. See [infra-setup.md](infra-setup.md) for the full setup.

```bash
docker compose -f infra/docker-compose.yml ps     # check status
docker compose -f infra/docker-compose.yml down    # stop the stack
```

---

## Troubleshooting

### "Cannot connect to the API at \<url\>"

The CLI can't reach its API endpoint. The default target is `http://localhost:8080` — check that the Docker stack (or another API) is running there:

```bash
docker compose -f infra/docker-compose.yml ps
```

If you set `CODEKNOW_API_URL`, check that:

- The URL is correct and includes the scheme (`http://` or `https://`).
- The server is actually running and reachable from your machine.
- There's no proxy or firewall blocking the connection.

### "Cannot connect to the daemon. Start it with: codeknow daemon start"

You're in **daemon mode** (`CODEKNOW_DAEMON=1`) but the daemon isn't running. Start it:

```bash
codeknow daemon start
```

If it just started, give it a moment — the CLI waits for the server to become ready, but a heavily loaded machine can be slow.

### Port already in use

The default port is `8080`. If something else is using it, pick another (daemon mode):

```bash
export CODEKNOW_DAEMON=1
export CODEKNOW_API_PORT=8181
codeknow daemon start
```

### Daemon won't stop

Force it (daemon mode):

```bash
codeknow daemon stop --force
```

### Checking which mode you're in

```bash
codeknow info
```

- `API: <url> (remote)` → remote mode (default). If the URL is `http://localhost:8080` it's the local Docker stack.
- `Daemon: running (PID …)` → local daemon mode.
