#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
if [[ -x "$ROOT/engine/.venv/bin/python" ]]; then
  PY="$ROOT/engine/.venv/bin/python"
fi

if ! "$PY" -c "import pytest" >/dev/null 2>&1; then
  echo "pytest not installed in $PY; run: $PY -m pip install pytest" >&2
  exit 1
fi

echo "== engine tests =="
PYTHONPATH="$ROOT/engine/src" "$PY" -m pytest -q "$ROOT/engine/tests"

echo "== chatbridge tests =="
cd "$ROOT/chatbridge"
"$PY" -m unittest discover -s tests -p 'test_*.py'

echo "== sample contract =="
"$PY" core/validate_spool.py spool-dev/sample --strict
