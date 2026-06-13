# Using CodeKnow

CodeKnow has two ways to run:

- **Local daemon mode** (default) — the CLI starts and manages a background API server on your machine.
- **Remote mode** — the CLI talks to a `codeknow-api` instance running elsewhere (another host, a shared server, or the cloud).

Every command below works the same in both modes. The only difference is *where the API server lives*.

---

## Commands at a glance

| Command | What it does |
|---|---|
| `codeknow add <ssh-url>` | Index a GitHub repo by its SSH URL |
| `codeknow remove <slug>` | Remove an indexed repo by slug |
| `codeknow search "<query>"` | Search the code index (use `--slug` to filter, repeatable) |
| `codeknow info` | Show connection status and indexed repo slugs |
| `codeknow clean` | Remove cached repos, graph output, and temp files (`-y` skips confirm) |
| `codeknow daemon <action>` | Manage the background service (local mode only) |

---

## Local daemon mode (default)

In this mode the CLI launches the `codeknow-api` server as a background process, tracks it via a PID file (`/tmp/codeknow-daemon.pid`), and connects to it over HTTP.

### Start, stop, and check the daemon

```bash
codeknow daemon start     # launch the API server in the background
codeknow daemon status    # is it running?
codeknow daemon restart   # restart the service
codeknow daemon stop      # stop the service
```

By default the daemon runs `codeknow-api` on `localhost:8080`.

### A full walkthrough

```bash
# 1. Start the background daemon
codeknow daemon start

# 2. Index a repo (output shows the generated slug, node count, and edge count)
codeknow add git@github.com:owner/repo.git

# 3. Search across all indexed repos
codeknow search "how does auth work"

# 4. Filter a search to specific repos
codeknow search "database connection" --slug owner-repo --slug other-repo

# 5. See status and available slugs
codeknow info

# 6. Stop the daemon when you're done
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

## Remote mode

Point the CLI at any running `codeknow-api` instance by setting one environment variable:

```bash
export CODEKNOW_API_URL=https://api.example.com
```

Now every command talks to that server instead of a local process:

```bash
codeknow add git@github.com:owner/repo.git
codeknow search "how does auth work"
codeknow info        # prints: API: https://api.example.com (remote)
```

### How remote mode differs

- **No local daemon is spawned.** `codeknow daemon start` / `stop` / `restart` are no-ops and print `"You are in remote mode"`.
- **`codeknow info`** reports the remote URL rather than a local PID.
- **`codeknow clean`** still clears local on-disk caches, but it will not try to stop a local daemon.
- `codeknow-api` does **not** need to be on your `$PATH` in remote mode.

### When to use it

- A shared/team `codeknow-api` instance is already running on another host.
- You're connecting to a cloud-hosted CodeKnow endpoint.
- You want to index and search without running anything locally.

To return to local daemon mode, just unset the variable:

```bash
unset CODEKNOW_API_URL
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CODEKNOW_API_URL` | *(unset → local mode)* | Full base URL of a remote API. When set, enables **remote mode** and overrides host/port. |
| `CODEKNOW_HOST` | `localhost` | API server host (local mode) |
| `CODEKNOW_API_PORT` | `8080` | API server port (local mode) |

### Precedence

1. `CODEKNOW_API_URL` — if set, remote mode is active and everything else is ignored.
2. `CODEKNOW_HOST` / `CODEKNOW_API_PORT` — apply in local mode.
3. Built-in defaults (`localhost` / `8080`).

Example — run the local daemon on a non-default port:

```bash
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

Point a CLI at a server you started manually by setting `CODEKNOW_API_URL`, or by matching `CODEKNOW_HOST`/`CODEKNOW_API_PORT`:

```bash
# Start a server on a custom port...
codeknow-api --port 8181 &

# ...and point the local CLI at it
export CODEKNOW_API_PORT=8181
codeknow info
```

---

## Troubleshooting

### "Cannot connect to the daemon. Start it with: codeknow daemon start"

You're in local mode but the daemon isn't running. Start it:

```bash
codeknow daemon start
```

If it just started, give it a moment — the CLI waits for the server to become ready, but a heavily loaded machine can be slow.

### "Cannot connect to the API at \<url\>"

You're in remote mode and the CLI can't reach `CODEKNOW_API_URL`. Check that:

- The URL is correct and includes the scheme (`http://` or `https://`).
- The server is actually running and reachable from your machine.
- There's no proxy or firewall blocking the connection.

### Port already in use

The default port is `8080`. If something else is using it, pick another:

```bash
export CODEKNOW_API_PORT=8181
codeknow daemon start
```

### Daemon won't stop

Force it:

```bash
codeknow daemon stop --force
```

### Checking which mode you're in

```bash
codeknow info
```

- `Daemon: running (PID …)` → local daemon mode.
- `API: <url> (remote)` → remote mode.
