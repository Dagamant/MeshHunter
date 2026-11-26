"""Command-line entry points.

  meshhunter            desktop GUI (PySide6); --tui / --headless /
                        --list-devices hand over to the terminal front end
  meshhunter-tui        terminal front end (TUI by default, or --headless)
  python -m meshhunter  same as `meshhunter`

The terminal front ends don't import Qt, so they also run where PySide6
isn't installed (pip install meshhunter without the [gui] extra, or
requirements-cli.txt).
"""

import importlib.util
import sys

TERMINAL_FLAGS = ("--tui", "--headless", "--list-devices")
GUI_PACKAGES = ("PySide6", "qasync", "numpy")

GUI_MISSING = """\
MeshHunter's desktop GUI needs the [gui] extra (PySide6, qasync, numpy),
which isn't installed ({error}).

  Installed with pipx:      pipx install --force "meshhunter[gui] @ git+https://github.com/Dagamant/MeshHunter"
  Running from a checkout:  ./run.sh   (Windows: run.bat)

For the terminal interface, run `meshhunter-tui` (or `meshhunter --tui`).
"""


def tui_main():
    from .tui.app import main as tui

    return tui()


def main():
    if any(arg in TERMINAL_FLAGS for arg in sys.argv[1:]):
        return tui_main()
    try:
        from .gui_qt.app import main as gui
    except ImportError as exc:
        # qasync raises its own ImportError (no .name) when PySide6 is
        # missing, so check what's actually installed.
        missing = [m for m in GUI_PACKAGES if importlib.util.find_spec(m) is None]
        if missing:
            sys.stderr.write(GUI_MISSING.format(error=f"missing {', '.join(missing)}"))
            return 1
        raise exc
    return gui()


if __name__ == "__main__":
    sys.exit(main())
