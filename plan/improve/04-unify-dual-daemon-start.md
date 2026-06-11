# Candidate 4: Unify the dual daemon-start paths

**Strength:** Worth exploring
**Dependency category:** in-process

## Files involved

| File | Lines | Role |
|---|---|---|
| `codeknow_cli/daemon/server.py` | 18 | Launches codeknow-api via subprocess.run |
| `codeknow_cli/daemon/fake_server.py` | 98 | Stub HTTP server for testing |
| `codeknow_cli/daemon_manager.py` | 110 | Process lifecycle (start, stop, PID, health check) |
| `codeknow_cli/endpoint.py` | 83 | Resolves API endpoint config (remote vs local) |
| `codeknow_cli/client.py` | 327 | Combines daemon lifecycle + API calls |
| `codeknow_cli/main.py` | 219 | Click CLI entry point (daemon command) |

## Problem: Two disjoint paths to start the daemon

### Path 1: CLI `codeknow daemon start`

```
main.py (daemon command)
  └── daemonocle.DaemonCLI
        └── calls server.run_server(host, port)
              └── shutil.which("codeknow-api")
              └── subprocess.run(["codeknow-api", "--host", host, "--port", str(port)])
```

The `daemonocle` library manages PID files, daemonization (double-fork), and signal handling. It calls `run_server()` as the "worker function" — a plain function that subprocesses the actual API binary.

### Path 2: Programmatic `Client.start_daemon()`

```
client.py (start_daemon method)
  └── endpoint.py resolve_endpoint()
        └── shutil.which("codeknow-api")
        └── builds worker_command: ["python", "-c", "from codeknow_cli.daemon.fake_server import run_server; run_server(...)"]
           OR ["codeknow-api", "--host", host, "--port", str(port)]
  └── daemon_manager.py DaemonManager(worker_command)
        └── subprocess.Popen(worker_command)
        └── writes PID file manually
        └── polls for readiness via _wait_for_ready()
```

### Key differences

| Aspect | Path 1 (daemonocle) | Path 2 (DaemonManager) |
|---|---|---|
| Binary discovery | `shutil.which` inside `server.run_server()` | `shutil.which` inside `endpoint.py` |
| Process launch | `subprocess.run` (blocking, daemonized by daemonocle) | `subprocess.Popen` (non-blocking, tracked) |
| PID management | daemonocle (automatic) | Manual PID file writes |
| Health check | None (daemonocle returns) | `_wait_for_ready()` polls `/v1/repos` |
| Stop mechanism | daemonocle SIGTERM | `DaemonManager._stop_tracked()` or `_stop_by_pid()` |
| Fake server support | None | `FAKE_SERVER` env var in endpoint.py |

## Specific friction points

### 1. `fake_server` reference in production code

`endpoint.py` constructs a Python command string that imports `codeknow_cli.daemon.fake_server`:

```python
if os.environ.get("FAKE_SERVER"):
    worker_command = [
        sys.executable, "-c",
        f"from codeknow_cli.daemon.fake_server import run_server; run_server('{host}', {port})"
    ]
```

The `FAKE_SERVER` env var is a test-only concern leaking into production endpoint resolution code.

### 2. Two different binary discovery patterns

- `server.py`: `shutil.which("codeknow-api")` inside `run_server()`
- `endpoint.py`: `shutil.which("codeknow")` (not `codeknow-api`!) — wait, actually it's `shutil.which("codeknow-api")`

Both call `shutil.which("codeknow-api")`, but in different files at different times. If the binary name changes, both must be updated.

### 3. DaemonManager's disjoint stop paths

```python
class DaemonManager:
    def stop(self, timeout=5.0):
        if self._proc:
            self._stop_tracked(timeout)   # terminate/wait/kill tracked process
        else:
            self._stop_by_pid(timeout)    # SIGTERM by PID from file
```

`is_running()` also branches:
- Tracked process: `self._proc.poll() is None`
- Untracked: `daemonocle` check

The same class behaves differently based on invisible internal state (`self._proc`).

### 4. Shallow: `server.py` (18 lines)

The entire module is one function:

```python
def run_server(host="127.0.0.1", port=9999):
    api_bin = shutil.which("codeknow-api")
    if not api_bin:
        raise ConfigError(...)
    subprocess.run([api_bin, "--host", host, "--port", str(port)])
```

Interface ≈ implementation. It exists solely as a callable for daemonocle.

## Proposed solution

Unify into a single **`DaemonLauncher`** that both the CLI and Client delegate to.

### Interface

```python
class DaemonLauncher:
    def __init__(self, host: str = "127.0.0.1", port: int = 8080, pid_file: str = DEFAULT_PID_FILE): ...

    def start(self, timeout: float = 30.0) -> int: ...   # returns PID
    def stop(self, timeout: float = 5.0) -> None: ...
    def is_running(self) -> bool: ...
    def read_pid(self) -> int | None: ...
```

### What it absorbs

- Binary discovery (`shutil.which("codeknow-api")`) — in one place
- Process spawning (`subprocess.Popen`) — consistent mechanism
- PID file management — one pattern
- Health check polling — always runs after start
- Stop logic — one path (tracked process with PID file fallback)

### How the two callers adapt

**CLI daemon command:**
```python
# main.py
@cli.command()
def daemon():
    launcher = DaemonLauncher(host=host, port=port)
    launcher.start()
```

The daemonocle integration can remain as a thin adapter if double-fork daemonization is needed, but it delegates to `DaemonLauncher` for the actual process start.

**Programmatic Client:**
```python
# client.py
class Client:
    def start_daemon(self, timeout=30):
        self._launcher = DaemonLauncher(host=self._host, port=self._port)
        pid = self._launcher.start(timeout)
        return DaemonStartResult(status="ok", pid=pid)
```

### Fake server handling

Move the `FAKE_SERVER` logic out of `endpoint.py`. Options:

1. **Environment-based override in DaemonLauncher**: `DaemonLauncher` checks `FAKE_SERVER` env var and constructs the fake server command instead. This keeps the test concern in one place.
2. **Separate test fixture**: Tests inject a `DaemonLauncher` subclass that uses `fake_server`. No env var needed in production code.

Option 2 is cleaner — the `FAKE_SERVER` env var disappears from production code entirely.

## Wins

- **locality**: process lifecycle logic in one module, not split across 3
- **delete server.py** (shallow: 18 lines, interface ≈ implementation)
- **eliminate fake_server reference** in production endpoint.py
- **leverage**: one module serves both CLI and programmatic paths
- **consistent behavior**: same start, stop, health-check, PID management regardless of entry point

## Testing improvements

- Tests exercise `DaemonLauncher` directly instead of testing `DaemonManager` + `server.py` separately
- Fake server mode is a test fixture, not an env var leak
- `is_running()` has one implementation, not two branches
- Stop logic is one path, not `_stop_tracked` vs `_stop_by_pid`

## Risks / considerations

- **daemonocle double-fork**: The CLI path uses daemonocle for proper Unix daemonization. If this is required, daemonocle can wrap `DaemonLauncher` rather than being replaced by it. The key is that process spawning and binary discovery happen in one place.
- **`daemonocle` is a dependency**: It's used for `DaemonCLI` (the Click subcommand group) and for PID-based `is_running()`. If replaced, the Click daemon commands need a reimplementation. Check if daemonocle is worth keeping or if a simpler approach (PID file + subprocess) suffices.
- **Backward compatibility**: If users have existing PID files from the daemonocle path, the new launcher must be able to read and stop those processes.
