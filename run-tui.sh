#!/usr/bin/env bash
# Terminal MeshHunter: full-screen TUI by default, or pass --headless.
# Creates the venv from requirements-cli.txt (no Qt) if it doesn't exist.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$DIR/venv/bin/python3"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "venv not found at $DIR/venv -- creating one (terminal-only deps)..." >&2
    python3 -m venv "$DIR/venv" || {
        echo "Failed to create venv (Debian/Ubuntu: sudo apt install python3-venv)" >&2
        exit 1
    }
    "$VENV_PYTHON" -m pip install -r "$DIR/requirements-cli.txt" || {
        echo "Failed to install dependencies" >&2
        exit 1
    }
fi

case " $* " in
    *" --headless "*|*" --list-devices "*) exec "$VENV_PYTHON" "$DIR/main.py" "$@" ;;
    *) exec "$VENV_PYTHON" "$DIR/main.py" --tui "$@" ;;
esac
