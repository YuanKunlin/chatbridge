#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHAT_RUNTIME="${CHATBRIDGE_RUNTIME:-$HOME/chatbridge}"
SEARCH_RUNTIME="${AIMEM_RUNTIME:-$HOME/AI-Advaned-File-Search}"

if [[ ! -d "$CHAT_RUNTIME" ]]; then
  echo "missing runtime dir: $CHAT_RUNTIME" >&2
  exit 1
fi
if [[ ! -d "$SEARCH_RUNTIME/src" ]]; then
  echo "missing search runtime: $SEARCH_RUNTIME/src" >&2
  exit 1
fi

echo "deploy chatbridge -> $CHAT_RUNTIME"
for sub in adapters core hub tests; do
  mkdir -p "$CHAT_RUNTIME/$sub"
  rsync -a --exclude __pycache__ "$ROOT/chatbridge/$sub/" "$CHAT_RUNTIME/$sub/"
done

echo "deploy engine -> $SEARCH_RUNTIME/src/aimem"
mkdir -p "$SEARCH_RUNTIME/src/aimem"
rsync -a --exclude __pycache__ "$ROOT/engine/src/aimem/" "$SEARCH_RUNTIME/src/aimem/"

echo "done; restart the search service if code changed."
