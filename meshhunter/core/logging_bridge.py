"""Routes stdlib logging records (from the meshcore/bleak libraries) into
the same ui_queue every other background operation in this app uses."""

import logging
import time


class QueueLogHandler(logging.Handler):
    """Routes stdlib logging records into the terminal.

    meshcore/bleak's own diagnostics (device scanning, GATT/service lookup,
    pairing, disconnect reasons) go through Python's logging module, which
    nothing in this app configures a destination for -- so a failed
    connection (BLE especially) showed up as one opaque line ("Could not
    connect to ...") with none of the detail the libraries actually logged
    about why. INFO level: reader.py (the busiest, per-packet module) uses
    debug/warning/error but zero info calls, while the connection-lifecycle
    code uses info for exactly the "what's happening" narrative -- so this
    stays quiet during normal operation and only speaks up around connects.
    """

    def __init__(self, ui_queue):
        super().__init__()
        self.ui_queue = ui_queue

    def emit(self, record):
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        self.ui_queue.put(("log", f"[{ts}] {record.levelname}: {record.name}: {record.getMessage()}"))


def attach_library_logging(ui_queue):
    handler = QueueLogHandler(ui_queue)
    handler.setLevel(logging.INFO)
    for name in ("meshcore", "bleak"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
