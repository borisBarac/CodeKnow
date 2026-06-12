#!/usr/bin/env bash
## Used for e2e tests
set -e

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is not installed."
  exit 1
fi

if brew ls --versions ripgrep >/dev/null 2>&1; then
  echo "ripgrep is already installed."
else
  echo "Installing ripgrep..."
  brew install ripgrep
fi