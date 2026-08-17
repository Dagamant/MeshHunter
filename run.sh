#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$DIR/venv/bin/python3"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "venv not found at $DIR/venv -- creating one..." >&2
    python3 -m venv "$DIR/venv" || {
        echo "Failed to create venv (Debian/Ubuntu: sudo apt install python3-venv)" >&2
        exit 1
    }
    echo "Installing dependencies from requirements.txt..." >&2
    "$VENV_PYTHON" -m pip install -r "$DIR/requirements.txt" || {
        echo "Failed to install dependencies" >&2
        exit 1
    }
    echo "venv ready." >&2
fi

exec "$VENV_PYTHON" "$DIR/main.py" "$@"
