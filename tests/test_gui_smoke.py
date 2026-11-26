import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qasync")

SCRIPT = Path(__file__).with_name("gui_smoke.py")


@pytest.mark.parametrize("kind", ["desktop", "pocket", "hat"])
def test_window_starts_and_closes(kind, tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=str(tmp_path),
               XDG_DATA_HOME=str(tmp_path / "data"), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"))
    result = subprocess.run([sys.executable, str(SCRIPT), kind], env=env, capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{kind}: ok" in result.stdout
