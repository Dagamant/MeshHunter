"""PySide6 entry point.

Runs Qt's event loop merged with asyncio via qasync, so MeshCoreWorker
(see core/worker.py) can run the MeshCore connection as a task on the same
loop this window's own event handling runs on, instead of on a separate
OS thread. GPSWorker is unaffected -- it's still a real thread either way.
"""

import asyncio
import sys

import qasync
from PySide6.QtWidgets import QApplication

from ..core import ble_compat
from ..core.paths import migrate_legacy_data
from .main_window import MainWindow
from .theme import STYLESHEET


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

    window = MainWindow()
    if migrated:
        window._log_line(f"-- Migrated legacy data files: {', '.join(migrated)} --")
    window.show()

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
