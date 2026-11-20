"""Thread-safe delivery from background workers (MeshCoreWorker, GPSWorker,
BLE scan thread, upload threads) into the Qt GUI thread.

MeshCoreWorker and every core.* function that reports progress (see
core.worker, core.devices.scan_ble_devices_sync, core.uploads) take a
"ui_queue" argument and only ever call ui_queue.put((kind, payload)) on it
-- they don't care whether that's a real queue.Queue or something else, so
handing them a Bridge instead needs no changes to any of that code (the
plan's "MeshCoreWorker untouched" requirement).

Signal emission is inherently thread-safe in Qt: a signal emitted from any
thread and connected (with the default AutoConnection) to a slot owned by
a QObject living on the GUI thread is automatically queued onto that
thread's event loop. That's what replaces the old ui_queue + 200ms
tk.after polling loop outright -- no manual QMetaObject.invokeMethod needed.
"""

from PySide6.QtCore import QObject, Signal


class Bridge(QObject):
    status = Signal(str)
    error = Signal(str)
    # object, not dict/list: PySide6 tries to coerce a plain `dict`/`list`
    # signal argument into a QVariantMap/QVariantList, and QVariantMap keys
    # must be strings -- our node dict is keyed by the int adv-type
    # constants, which fails that coercion silently (a
    # "_pythonToCppCopy: Cannot copy-convert ... to C++" warning, with the
    # emit just dropped). `object` passes the Python value through as-is.
    nodes = Signal(object)
    ble_devices = Signal(object)
    log = Signal(str)
    # LoRa sniffer waterfall feed (see core/sniffer_worker.py):
    # sniffer_profiles: [{"name", "freq", "bw", "sf"}] whenever (re)configured
    # sniffer_rssi: {"cur": idx, "v": [[idx, dBm], ...]} ~5x per second
    # sniffer_event: {"p": idx, "kind": "ok"|"crc"|"partial"} per packet
    sniffer_profiles = Signal(object)
    sniffer_rssi = Signal(object)
    sniffer_event = Signal(object)
    # sniffer_stat: radio telemetry ~every 5s -- noise floor, RX counters,
    # hop count -- for the live stats strip and the noise-floor graph.
    sniffer_stat = Signal(object)
    message = Signal(object)
    sniffer_radios = Signal(object)
    wdgwars_sent = Signal(int)
    ingest_sent = Signal(int)

    def put(self, item):
        """queue.Queue-compatible sink: put((kind, payload))."""
        kind, payload = item
        getattr(self, kind).emit(payload)
