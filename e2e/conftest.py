import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def pytest_configure(config):
    env_file = Path(os.environ.get("E2E_ENV_FILE", str(_HERE / ".env.e2e")))
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())
