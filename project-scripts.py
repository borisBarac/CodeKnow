"""CLI entry-points registered via ``[project.scripts]`` in codeknow-cli."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def dev_check() -> None:
    """Run all static checks: ruff lint, ruff format, mypy."""
    parser = argparse.ArgumentParser(description="Run all static checks.")
    parser.add_argument("--fix", action="store_true", help="Apply fixable violations.")
    parser.add_argument(
        "--unsafe-fixes",
        action="store_true",
        help="Include fixes that may change runtime behaviour (66 hidden fixes).",
    )
    parser.parse_args()

    ruff_check_cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--fix",
        "--unsafe-fixes",
        ".",
    ]

    steps = [
        ("ruff check", ruff_check_cmd),
        (
            "ruff format",
            [sys.executable, "-m", "ruff", "format", "."],
        ),
        ("mypy", [sys.executable, "-m", "mypy", "packages"]),
    ]

    failed: list[str] = []
    for label, cmd in steps:
        result = subprocess.run(cmd, check=False)  # noqa: S603
        if result.returncode != 0:
            failed.append(label)

    if failed:
        sys.exit(1)
    else:
        pass


def run_pipeline_cli() -> None:
    """Run the codeknow pipeline on a GitHub repository."""
    from codeknow.pipeline import PipelineConfig, run_pipeline

    parser = argparse.ArgumentParser(
        description="Run the codeknow pipeline on a GitHub repository.",
    )
    parser.add_argument(
        "--repo-url",
        required=True,
        help="GitHub repository URL (e.g. https://github.com/OWNER/REPO)",
    )
    parser.add_argument(
        "--input-dir",
        default=None,
        type=Path,
        help=(
            "Directory to clone repos into "
            "(default: $CODEKNOW_INPUT_DIR or ./.codeknow/repos)"
        ),
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=None,
        type=Path,
        help="Output directory (default: $CODEKNOW_OUTPUT_DIR or ./codeknow-out)",
    )
    parser.add_argument(
        "-g",
        "--graph-file",
        default="graph.json",
        help="Filename for the graph output (default: graph.json)",
    )
    parser.add_argument(
        "--chunk-map-file",
        default="chunk_map.json",
        help="Filename for the chunk map output (default: chunk_map.json)",
    )
    args = parser.parse_args()

    config = PipelineConfig(
        repo_url=args.repo_url,
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        graph_filename=args.graph_file,
        chunk_map_filename=args.chunk_map_file,
    )

    run_pipeline(config)


def gen_client() -> None:
    """Generate a Python HTTP client from the CodeKnow API OpenAPI spec."""
    from codeknow_api.app import create_app

    parser = argparse.ArgumentParser(
        description="Generate a Python HTTP client from the CodeKnow API OpenAPI spec.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory where the generated client will be saved (must exist).",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()

    if not output_dir.is_dir():
        print(f"Error: output directory does not exist: {output_dir}")  # noqa: T201
        sys.exit(1)

    app = create_app()
    schema = app.openapi()

    with tempfile.TemporaryDirectory() as tmp:
        spec_path = Path(tmp) / "openapi.json"
        spec_path.write_text(json.dumps(schema, indent=2))
        print(f"OpenAPI schema written to {spec_path}")  # noqa: T201

        cmd = [
            sys.executable,
            "-m",
            "openapi_python_client",
            "generate",
            "--path",
            str(spec_path),
            "--output-path",
            str(output_dir),
            "--overwrite",
        ]
        print(f"+ {' '.join(cmd)}")  # noqa: T201
        subprocess.check_call(cmd)  # noqa: S603

    print(f"Client generated at {output_dir}")  # noqa: T201


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: project-scripts.py <command>")  # noqa: T201
        sys.exit(1)
    cmd = sys.argv.pop(1)
    if cmd == "dev-check":
        dev_check()
    elif cmd == "pipeline":
        run_pipeline_cli()
    elif cmd == "gen-client":
        gen_client()
    else:
        print(f"Unknown command: {cmd}")  # noqa: T201
        sys.exit(1)


if __name__ == "__main__":
    main()
