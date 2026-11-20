"""PySide6 entry point.

Runs Qt's event loop merged with asyncio via qasync, so MeshCoreWorker
(see core/worker.py) can run the MeshCore connection as a task on the same
loop this window's own event handling runs on, instead of on a separate
OS thread. GPSWorker is unaffected -- it's still a real thread either way.
"""

import asyncio
import sys
from pathlib import Path

import qasync
from PySide6.QtWidgets import QApplication

from ..core import ble_compat
from ..core.paths import migrate_legacy_data
from .main_window import MainWindow
from .hat_pocket_window import PocketWindow
from .pocket_window import PocketMainWindow
from .theme import STYLESHEET


# The LoRa HAT's SX1262 (spi1-1cs overlay). Its presence means this is a
# HAT device, which gets the fullscreen HAT touch UI unless --desktop or
# --pocket is given.
HAT_SPIDEV = Path("/dev/spidev1.0")


def _use_hat_ui(argv):
    if "--desktop" in argv or "--pocket" in argv:
        return False
    return "--hat" in argv or (sys.platform.startswith("linux") and HAT_SPIDEV.exists())


def main():
    ble_compat.apply_ble_workarounds()
    migrated = migrate_legacy_data()

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    # QEventLoop makes this one loop drive both Qt's event processing and
    # asyncio coroutines/tasks -- everything scheduled via
    # asyncio.ensure_future() from here on (MeshCoreWorker included) runs
    # on the same thread as the GUI itself.
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    # Three layouts:
    #   HAT touch UI (hat_pocket_window.py) -- fullscreen, auto-picked when
    #     the LoRa HAT is present, or forced with --hat
    #   --pocket -- compact touch-friendly version of the main window for a
    #     fixed 640x480 display (pocket_window.py)
    #   otherwise -- the normal resizable desktop rail+content layout
    if _use_hat_ui(sys.argv):
        window = PocketWindow()
        window.showFullScreen()
    else:
        window = PocketMainWindow() if "--pocket" in sys.argv else MainWindow()
        if migrated:
            window._log_line(f"-- Migrated legacy data files: {', '.join(migrated)} --")
        window.show()

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
