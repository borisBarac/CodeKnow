#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

usage() {
  cat <<'EOF'
Usage: evals/run_fastify_eval.sh <command>

Commands:
  build   Build or reuse the Fastify graph + Chroma index
  smoke   Run the eval on the first item only (SMOKE=1)
  eval    Run the full eval (all items)
  all     Run build -> smoke -> eval in sequence
  help    Show this help (-h / --help also work)

No arguments prints this help and exits 0.
EOF
}

run_build() {
  printf '%s\n' 'Building Fastify eval index...'
  FORCE_REINDEX=1 uv run python evals/build_fastify_graph.py
}

run_smoke() {
  printf '%s\n' 'Running smoke eval...'
  SMOKE=1 EVAL_SEEDS=3 uv run python evals/eval_fastify.py
}

run_eval() {
  printf '%s\n' 'Running full eval...'
  SMOKE=0 uv run python evals/eval_fastify.py
}

cmd="${1:-help}"
case "${cmd}" in
  build)
    run_build
    ;;
  smoke)
    run_smoke
    ;;
  eval)
    run_eval
    ;;
  all)
    run_build
    run_smoke
    run_eval
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    printf 'Unknown command: %s\n\n' "${cmd}" >&2
    usage >&2
    exit 1
    ;;
esac
