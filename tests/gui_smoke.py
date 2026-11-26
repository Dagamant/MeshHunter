"""Build each top-level window offscreen, let it run briefly, and close it.

Run in its own process (see test_gui_smoke.py) so the data/config paths,
which are resolved at import time, point into a throwaway directory.

    python tests/gui_smoke.py desktop|pocket|hat
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import qasync  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from meshhunter.gui_qt.hat_pocket_window import PocketWindow  # noqa: E402
from meshhunter.gui_qt.main_window import MainWindow  # noqa: E402
from meshhunter.gui_qt.pocket_window import PocketMainWindow  # noqa: E402
from meshhunter.gui_qt.theme import STYLESHEET  # noqa: E402

WINDOWS = {"desktop": MainWindow, "pocket": PocketMainWindow, "hat": PocketWindow}


def main(kind):
    errors = []
    sys.excepthook = lambda *exc: errors.append(exc)
    app = QApplication([sys.argv[0]])
    app.setStyleSheet(STYLESHEET)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    window = WINDOWS[kind]()
    window.show()
    QTimer.singleShot(1500, window.close)
    QTimer.singleShot(2000, loop.stop)
    with loop:
        loop.run_forever()
    if errors:
        import traceback
        for exc in errors:
            traceback.print_exception(*exc)
        sys.exit(1)
    print(f"{kind}: ok")


if __name__ == "__main__":
    main(sys.argv[1])
