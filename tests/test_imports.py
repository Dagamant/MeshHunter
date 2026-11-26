"""Every non-GUI module imports with only requirements-cli.txt installed,
and the Qt GUI modules import when PySide6 is available."""

import importlib
import pkgutil

import pytest

import meshhunter.core
import meshhunter.tui

# Optional extras: modules that need a package outside requirements-cli.txt.
NEEDS = {"meshhunter.core.rtlsdr": "numpy"}


def _modules(package):
    return sorted(m.name for m in pkgutil.walk_packages(package.__path__, package.__name__ + "."))


@pytest.mark.parametrize("name", _modules(meshhunter.core) + _modules(meshhunter.tui))
def test_cli_modules_import(name):
    if name in NEEDS:
        pytest.importorskip(NEEDS[name])
    if name.startswith("meshhunter.tui."):
        pytest.importorskip("curses")  # not in the Windows standard library
    importlib.import_module(name)


def _gui_modules():
    try:
        import meshhunter.gui_qt as gui
    except ImportError:
        return []
    return _modules(gui)


@pytest.mark.parametrize("name", _gui_modules() or ["meshhunter.gui_qt"])
def test_gui_modules_import(name, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    importlib.import_module(name)
