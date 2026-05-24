from __future__ import annotations

import shutil
import subprocess


def run_server(host: str = "127.0.0.1", port: int = 9999) -> None:
    api_bin = shutil.which("codeknow-api")
    if api_bin is None:
        msg = "codeknow-api is not installed. Run: uv sync"
        raise RuntimeError(msg)

    subprocess.run(  # noqa: S603
        [api_bin, "--host", host, "--port", str(port)],
        check=False,
    )
