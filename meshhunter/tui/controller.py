"""Controller: everything MainWindow does, minus the widgets.

Owns the config, the device worker (MeshCoreWorker or SnifferWorker), the
GPS worker and the session log, and keeps the state the TUI draws: log
lines, decoded messages, the waterfall, node counts and upload totals.
The curses UI (screen.py) and the screenless headless mode (headless.py)
both sit on top of it.

Workers report through `ui_queue.put((kind, payload))` from either the
asyncio loop (MeshCoreWorker) or their own threads (SnifferWorker,
GPSWorker, uploads), so LoopBridge hops every report onto the loop before
touching state.

Unlike the GUI, a dropped or failed connection is retried every
RECONNECT_S seconds until the user disconnects, since a headless box has
nobody to press Connect again (e.g. the radio isn't plugged in yet at boot).
"""

import asyncio
import threading
from collections import deque
from pathlib import Path

from ..core.config import load_config, save_config
from ..core.constants import MESHTASTIC_COUNT_KEY, NODE_DISPLAY_TYPES, TERMINAL_MAX_LINES
from ..core.devices import format_ble_entry, list_devices, scan_ble_devices_sync
from ..core.gps import GPSState, GPSWorker
from ..core.json_import import import_contacts_json
from ..core.logging_bridge import attach_library_logging
from ..core.messages import MessageLog
from ..core.session import SessionLog, device_label, make_worker, session_log_name
from ..core.uploads import flush_pending_ingest, flush_pending_wdgwars
from ..core.waterfall_model import WaterfallModel

RECONNECT_S = 10


class LoopBridge:
    """ui_queue stand-in: hands every (kind, payload) to the controller on
    the asyncio loop, whichever thread it came from."""

    def __init__(self, controller):
        self.controller = controller
        self.loop = None

    def put(self, item, conn=None):
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self.controller.handle, item[0], item[1], conn)
        except RuntimeError:  # loop shutting down
            pass


class ConnectionQueue:
    """The ui_queue one worker gets: tags its reports with its connection
    number, so a late "Disconnected" from a worker the user already
    replaced can't tear down the new connection."""

    def __init__(self, bridge, conn):
        self.bridge = bridge
        self.conn = conn

    def put(self, item):
        self.bridge.put(item, self.conn)


class Controller:
    def __init__(self, device=None, reconnect=True):
        self.config = load_config()
        self.device = device or self.config.get("serial_device", "")
        self.reconnect = reconnect
        self.bridge = LoopBridge(self)
        self.worker = None
        self.gps_state = GPSState()
        self.gps_worker = None
        self.session_log = SessionLog()

        self.log = deque(maxlen=TERMINAL_MAX_LINES)
        self.messages = MessageLog()
        self.waterfall = WaterfallModel()
        self.counts = {}
        self.radios = []  # sniffer radio status (see SnifferWorker._radios_changed)
        self.sniffer_stat = {}  # radio label -> latest sniffer_stat payload
        self.connected = False
        self.status = "not connected"
        self.ble_entries = []
        self.wdgwars_sent = 0
        self.ingest_sent = 0

        self.log_listeners = []  # callables(line), e.g. headless stdout
        self.changed = asyncio.Event()  # set on any state change; the UI redraws
        self._want_connected = False
        self._retry = None
        self._conn = 0  # bumped per connection attempt

    # ---- lifecycle --------------------------------------------------------

    def start(self, autoconnect=False):
        self.bridge.loop = asyncio.get_running_loop()
        attach_library_logging(self.bridge)
        self.start_gps()
        if autoconnect:
            self.connect()

    async def shutdown(self):
        self._want_connected = False
        self._cancel_retry()
        worker = self.worker
        if worker is not None:
            worker.stop()
            self.worker = None
            # Give it a moment to close cleanly (the sniffer sends STOP,
            # the companion disconnects) before the loop goes away.
            for _ in range(30):
                if not worker.is_alive():
                    break
                await asyncio.sleep(0.1)
        if self.gps_worker is not None:
            self.gps_worker.stop()
        self.session_log.close()

    # ---- state changes from workers ---------------------------------------

    def handle(self, kind, payload, conn=None):
        if conn is not None and conn != self._conn and kind in ("status", "error"):
            return  # from a worker that's already been replaced
        handler = getattr(self, f"_on_{kind}", None)
        if handler is not None:
            handler(payload)
        self.changed.set()

    def log_line(self, line):
        self.log.append(line)
        self.session_log.write(line)
        for listener in self.log_listeners:
            listener(line)
        self.changed.set()

    def _on_log(self, payload):
        self.log_line(payload)

    def _on_status(self, payload):
        self.log_line(f"-- {payload} --")
        if payload.startswith("Connected"):
            self.connected = True
            self.status = payload
        elif payload == "Disconnected":
            self.connected = False
            self.status = "not connected"
            if self.worker is None:
                self.session_log.close()
            else:
                # The worker ended on its own: treat it like a dropped link.
                self.worker = None
                self._schedule_reconnect()

    def _on_error(self, payload):
        self.log_line(f"-- Error: {payload} --")
        self.connected = False
        self.status = "error"
        self.worker = None
        self._schedule_reconnect()

    def _on_nodes(self, payload):
        self.counts.update(payload)

    def _on_ble_devices(self, payload):
        self.ble_entries = [format_ble_entry(name, addr) for name, addr in payload]
        self.log_line(f"-- Found {len(payload)} BLE device(s) --")

    def _on_sniffer_profiles(self, payload):
        self.waterfall.set_profiles(payload)

    def _on_sniffer_radios(self, payload):
        self.radios = payload

    def _on_sniffer_stat(self, payload):
        # Replaces the old SNIFF_STAT log line: kept as live state the Radio
        # view shows, instead of scrolling past in the terminal.
        self.sniffer_stat[payload.get("radio") or ""] = payload

    def _on_sniffer_rssi(self, payload):
        self.waterfall.add_rssi(payload)

    def _on_sniffer_event(self, payload):
        self.waterfall.add_event(payload)

    def _on_message(self, payload):
        self.messages.add(payload)

    def _on_wdgwars_sent(self, payload):
        self.wdgwars_sent += payload

    def _on_ingest_sent(self, payload):
        self.ingest_sent += payload

    # ---- connection -------------------------------------------------------

    @property
    def is_sniffer(self):
        return bool(getattr(self.worker, "is_sniffer", False))

    @property
    def busy(self):
        """A worker exists: connected, or still connecting."""
        return self.worker is not None

    def devices(self):
        return list_devices() + self.ble_entries

    def connect(self, selection=None, fresh=True):
        selection = selection or self.device
        if not selection:
            self.log_line("-- Choose a device first (press d) --")
            return
        if self.worker is not None:
            return
        self._cancel_retry()
        self.device = selection
        self._want_connected = True
        if self.config.get("serial_device") != selection:
            self.config["serial_device"] = selection
            save_config(self.config)
        if fresh:
            self.counts = {}
            self.sniffer_stat = {}
            self.messages.clear()
            self.log.clear()
        self.waterfall.set_profiles([])
        self.radios = []
        self.status = "connecting"
        # A retry keeps writing to the same session log rather than
        # starting a new file every RECONNECT_S seconds.
        if fresh or self.session_log.file is None:
            error = self.session_log.open(session_log_name(selection))
            if error:
                self.log_line(error)
            elif self.session_log.path is not None:
                self.log_line(f"-- Logging serial terminal session to {self.session_log.path} --")
        self.log_line(f"-- Connecting to {device_label(selection)}... --")
        self._conn += 1
        queue = ConnectionQueue(self.bridge, self._conn)
        self.worker = make_worker(selection, self.config, queue, self.gps_state)
        if self.is_sniffer and len(self.worker.ports) == 1:
            self.log_line(f"-- {device_label(selection)} is a MeshHunter LoRa sniffer --")
        self.worker.start()

    def disconnect(self):
        self._want_connected = False
        self._cancel_retry()
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
        self._conn += 1  # whatever the old worker reports from here on is stale
        self.session_log.close()
        self.connected = False
        self.status = "not connected"
        self.changed.set()

    def toggle_connection(self):
        if self.worker is not None or self._retry is not None:
            self.disconnect()
        else:
            self.connect()

    def _schedule_reconnect(self):
        if not (self.reconnect and self._want_connected) or self._retry is not None:
            self.session_log.close()
            return
        self.log_line(f"-- Retrying in {RECONNECT_S} s --")
        self.status = "waiting to reconnect"
        self._retry = asyncio.get_running_loop().call_later(RECONNECT_S, self._do_reconnect)

    def _do_reconnect(self):
        self._retry = None
        if self._want_connected and self.worker is None:
            self.connect(fresh=False)
        self.changed.set()

    def _cancel_retry(self):
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None

    @property
    def retrying(self):
        return self._retry is not None

    # ---- GPS --------------------------------------------------------------

    def start_gps(self):
        if self.gps_worker is not None:
            self.gps_worker.stop()
            self.gps_worker = None
        if not self.config.get("gps_enabled", True):
            return
        port = self.config.get("gps_port", "").strip()
        if not port:
            return
        self.gps_worker = GPSWorker(port, self.gps_state, self.bridge)
        self.gps_worker.start()

    def gps_text(self):
        if not self.config.get("gps_enabled", True):
            return "disabled"
        if self.gps_worker is None:
            return "off"
        fix = self.gps_state.fix
        return f"{fix[0]:.5f}, {fix[1]:.5f}" if fix else "no fix"

    # ---- settings ---------------------------------------------------------

    def apply_settings(self, new_config):
        gps_before = (self.config.get("gps_port", ""), self.config.get("gps_enabled", True))
        self.config = new_config
        save_config(self.config)
        self.log_line("-- Config saved --")
        if self.worker is not None:
            self.worker.apply_config(dict(self.config))
        if (self.config.get("gps_port", ""), self.config.get("gps_enabled", True)) != gps_before:
            self.start_gps()

    # ---- actions ----------------------------------------------------------

    def send_advert(self, flood):
        if self.worker is None or not self.connected:
            self.log_line("-- Not connected --")
        elif self.is_sniffer:
            self.log_line("-- Not available: the sniffer firmware is receive-only --")
        else:
            self.worker.send_advert(flood)

    def clear_device_contacts(self):
        if self.worker is not None and self.connected:
            self.worker.clear_all_contacts()

    def send_pending(self):
        self.log_line("-- Sending pending batch to WDGWars... --")
        threading.Thread(target=flush_pending_wdgwars, args=(self.config, self.bridge), daemon=True).start()
        if self.config.get("ingest_url") and self.config.get("ingest_api_key"):
            self.log_line("-- Sending pending batch to Ingest API... --")
            threading.Thread(target=flush_pending_ingest, args=(self.config, self.bridge), daemon=True).start()

    def import_json(self, path):
        path = Path(path).expanduser()
        if not path.is_file():
            self.log_line(f"-- No such file: {path} --")
            return
        self.log_line(f"-- Importing contacts from {path.name}... --")
        threading.Thread(target=import_contacts_json, args=(str(path), self.bridge), daemon=True).start()

    def scan_ble(self):
        self.log_line("-- Scanning for BLE devices... --")
        threading.Thread(target=scan_ble_devices_sync, args=(self.bridge,), daemon=True).start()

    # ---- display helpers --------------------------------------------------

    def count_items(self):
        items = [(label, self.counts.get(t, 0)) for t, label in NODE_DISPLAY_TYPES]
        if MESHTASTIC_COUNT_KEY in self.counts:
            items.append(("Meshtastic", self.counts[MESHTASTIC_COUNT_KEY]))
        return items

    def device_text(self):
        return device_label(self.device) if self.device else "no device"
