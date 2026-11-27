"""pyproject.toml and the requirements files must list the same packages,
since run.sh / run-tui.sh install from the latter and pip/pipx from the
former."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import meshhunter
import meshhunter.__main__ as entry

ROOT = Path(__file__).resolve().parent.parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _requirements(name):
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    return {line.split("#")[0].strip().replace(" ", "") for line in lines if line.split("#")[0].strip()}


def _normalize(specs):
    return {s.replace(" ", "").replace("'", '"') for s in specs}


def test_base_dependencies_match_requirements_cli():
    # pycryptodome is imported directly, so the package pins it rather than
    # relying on meshcore pulling it in.
    pinned = _normalize(PROJECT["dependencies"]) - {"pycryptodome==3.23.0"}
    assert pinned == _normalize(_requirements("requirements-cli.txt"))


def test_base_plus_gui_matches_requirements_txt():
    combined = _normalize(PROJECT["dependencies"] + PROJECT["optional-dependencies"]["gui"])
    assert combined == _normalize(_requirements("requirements.txt"))


def test_entry_points_exist():
    scripts = PROJECT["scripts"]
    assert scripts == {"meshhunter": "meshhunter.__main__:main", "meshhunter-tui": "meshhunter.__main__:tui_main"}
    assert callable(entry.main) and callable(entry.tui_main)


def test_version():
    parts = meshhunter.__version__.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


def test_gui_assets_are_package_data():
    assets = Path(meshhunter.__file__).parent / "gui_qt" / "assets"
    for name in ("logo.png", "check.svg", "chevron_down.svg"):
        assert (assets / name).is_file()


def test_missing_gui_prints_help():
    # In a fresh interpreter, since PySide6 may already be loaded here.
    code = ("import sys; sys.modules['PySide6'] = None; sys.argv = ['meshhunter'];"
            "from meshhunter.__main__ import main; sys.exit(main())")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1, result.stderr
    assert "meshhunter[gui]" in result.stderr and "meshhunter-tui" in result.stderr


@pytest.mark.parametrize("flag", entry.TERMINAL_FLAGS)
def test_terminal_flags_go_to_tui(flag, monkeypatch):
    called = []
    monkeypatch.setattr(sys, "argv", ["meshhunter", flag])
    monkeypatch.setattr(entry, "tui_main", lambda: called.append(flag) or 0)
    assert entry.main() == 0 and called == [flag]


def test_versions_agree_and_have_changelog_entry():
    script = ROOT / ".github" / "scripts" / "release_info.py"
    result = subprocess.run([sys.executable, str(script), "version"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == meshhunter.__version__
    notes = subprocess.run([sys.executable, str(script), "notes", meshhunter.__version__],
                           capture_output=True, text=True, timeout=60)
    assert notes.returncode == 0, notes.stderr
