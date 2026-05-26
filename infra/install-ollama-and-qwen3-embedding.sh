#!/usr/bin/env bash
set -euo pipefail

MODEL="qwen3-embedding:4b"
OLLAMA_SERVE_PID=""

cleanup_ollama_serve() {
  if [[ -n "$OLLAMA_SERVE_PID" ]]; then
    kill "$OLLAMA_SERVE_PID" 2>/dev/null || true
  fi
}
trap cleanup_ollama_serve EXIT

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

os_name() {
  uname -s
}

install_ollama() {
  echo "Ollama not found."
  if have_cmd brew; then
    echo "Installing Ollama with Homebrew..."
    brew install --cask ollama
  else
    echo "Homebrew is not installed. Install Homebrew first or install Ollama manually from https://ollama.com/download" >&2
    exit 1
  fi
}

start_ollama_if_needed() {
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "Ollama server is already running."
    return
  fi

  echo "Starting Ollama server..."

  case "$(os_name)" in
    Linux)
      if have_cmd systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^ollama.service'; then
        sudo systemctl start ollama || true
      fi
      ;;
    Darwin)
      open -a Ollama || true
      ;;
  esac

  local elapsed=0
  local max_wait=30
  while [[ $elapsed -lt $max_wait ]]; do
    if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      echo "Ollama server is ready."
      OLLAMA_SERVE_PID=""
      return
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done

  echo "Server not detected after ${max_wait}s. Launching ollama serve directly..."
  nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
  OLLAMA_SERVE_PID=$!
  sleep 5

  if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "Ollama server did not become ready on 127.0.0.1:11434" >&2
    exit 1
  fi

  OLLAMA_SERVE_PID=""
}

ensure_model() {
  if ollama list | awk 'NR>1 {print $1}' | grep -Fxq "$MODEL"; then
    echo "Model $MODEL is already installed."
  else
    echo "Pulling model $MODEL ..."
    ollama pull "$MODEL"
  fi
}

main() {
  if have_cmd ollama; then
    echo "Ollama is already installed."
  else
    install_ollama
  fi

  if ! have_cmd ollama; then
    echo "Ollama installation appears to have failed." >&2
    exit 1
  fi

  start_ollama_if_needed
  ensure_model
  echo "Done."
}

main "$@"
