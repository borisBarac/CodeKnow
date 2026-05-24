from __future__ import annotations

import os
import subprocess
import sys


def run_server(host: str = "127.0.0.1", port: int = 9999) -> None:
    env = os.environ.copy()
    if os.getenv("CODEKNOW_STUB"):
        env["CODEKNOW_STUB"] = os.environ["CODEKNOW_STUB"]

    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "codeknow_api.app:create_app",
            "--factory",
            "--host",
            host,
            "--port",
            str(port),
        ],
        env=env,
        check=False,
    )
