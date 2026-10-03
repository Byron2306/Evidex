#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BOOTSTRAP="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BOOTSTRAP="python"
else
  echo "Python 3.10+ is required, but no python3 or python executable was found." >&2
  exit 1
fi

PY="$SCRIPT_DIR/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "Missing .venv. Creating it..."
  "$PYTHON_BOOTSTRAP" -m venv .venv
fi

"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements.txt
"$PY" -m pip install -e .

"$PY" -m evidence_pack_engine.desktop
