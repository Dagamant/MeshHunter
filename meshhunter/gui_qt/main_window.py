"""The main application window: owns the MeshCoreWorker/GPSWorker lifecycle,
the rail+content layout, and all the signal wiring between them.

MeshCoreWorker (core/worker.py) runs the MeshCore connection as a task on
the same qasync-merged event loop this window's own Qt event loop runs on
(see gui_qt/app.py), rather than on a separate thread. It just calls
ui_queue.put((kind, payload)); "ui_queue" is a Bridge (see bridge.py), so
those calls arrive as same-thread Qt signal deliveries.

GPSWorker stays a real OS thread (pyserial's reads are blocking, with no
asyncio-native equivalent) -- its Bridge signals still cross a real thread
boundary, which Qt's signal/slot connections handle safely regardless.

Configuration lives in a modal Settings dialog (see settings_dialog.py),
opened via the gear button in the rail's brand header -- the rail itself
only keeps operational controls (device selection, connect, advertise,
sync) and status/indicators that need to update continuously while the
dialog is closed.
"""

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.config import load_config, load_raw_config, save_config
from ..core.constants import SERIAL_BAUD
from ..core.devices import format_ble_entry, list_serial_ports, parse_device_selection, scan_ble_devices_sync
from ..core.gps import GPSState, GPSWorker
from ..core.logging_bridge import attach_library_logging
from ..core.paths import SESSION_LOG_DIR
from ..core.uploads import flush_pending_ingest, flush_pending_wdgwars
from ..core.worker import MeshCoreWorker
from . import dialogs
from .bridge import Bridge
from .settings_dialog import SettingsDialog
from .theme import ACCENT, DIM, LOGO_PATH, RAIL_WIDTH, pick_mono_font
from .widgets.advertise_panel import AdvertisePanel
from .widgets.common import make_button
from .widgets.connection_panel import ConnectionPanel
from .widgets.live_pill import LivePill
from .widgets.node_panel import NodePanel
from .widgets.sync_panel import SyncPanel
from .widgets.terminal_log import TerminalLog

# Optional/power-user config keys -- not part of example-config.json's
# standard set, so their settings-dialog sections (and this main-window GPS
# readout) only appear once the user has hand-added the relevant key(s) to
# config.json at least once.
_GPS_KEYS = ("gps_port", "gps_enabled")
_INGEST_KEYS = ("ingest_url", "ingest_api_key", "ingest_auto_send")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeshHunter")
        self.setWindowIcon(QIcon(str(LOGO_PATH)))
        self.resize(1280, 860)
        self.setMinimumSize(760, 560)

        self.config_data = load_config()
        raw_config = load_raw_config()
        self.gps_configured = any(key in raw_config for key in _GPS_KEYS)
        self.ingest_configured = any(key in raw_config for key in _INGEST_KEYS)

        self.bridge = Bridge()
        attach_library_logging(self.bridge)
        self.worker = None
        self.wdgwars_sent_total = 0
        self.ingest_sent_total = 0
        self.session_log_file = None
        self.session_log_path = None
        self._ble_entries = []
        self._settings_dialog = None

        # GPS is independent of the MeshCore connection -- separate
        # hardware, own lifecycle -- so gps_state exists unconditionally
        # (MeshCoreWorker reads it regardless of whether GPS is configured;
        # .fix just stays None) and the worker thread only starts if a port
        # is actually set.
        self.gps_state = GPSState()
        self.gps_worker = None

        self._build_shortcuts()
        self._build_widgets()
        self._wire_signals()

        self.connection_panel.set_devices(list_serial_ports() + self._ble_entries)
        self.connection_panel.set_device_selection(self.config_data.get("serial_device", ""))
        self._update_send_pending_visibility()

        self._gps_display_timer = QTimer(self)
        self._gps_display_timer.timeout.connect(self._update_gps_display)
        self._gps_display_timer.start(1000)

        QTimer.singleShot(500, self._scan_for_ble)
        self._start_gps_worker()
        self._update_gps_display()

    # ---- layout ---------------------------------------------------------

    def _build_shortcuts(self):
        # No menu bar -- Settings is reached via the rail's gear button
        # (see _build_brand_header) -- but the keyboard shortcut is still
        # worth keeping, so the QAction is attached directly to the window.
        prefs_action = QAction("Preferences", self)
        prefs_action.setShortcut(QKeySequence("Ctrl+,"))
        prefs_action.triggered.connect(self._open_settings_dialog)
        self.addAction(prefs_action)

    def _build_widgets(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_rail())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.terminal_log = TerminalLog()
        content_layout.addWidget(self.terminal_log, stretch=1)

        self.node_panel = NodePanel()
        content_layout.addWidget(self.node_panel)

        root.addWidget(content, stretch=1)

    def _build_rail(self):
        scroll = QScrollArea()
        scroll.setObjectName("rail")
        scroll.setFixedWidth(RAIL_WIDTH)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        rail = QWidget()
        rail.setObjectName("railContent")
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_brand_header())

        self.connection_panel = ConnectionPanel()
        layout.addWidget(self.connection_panel)

        self.advertise_panel = AdvertisePanel()
        layout.addWidget(self.advertise_panel)

        self.sync_panel = SyncPanel()
        layout.addWidget(self.sync_panel)

        layout.addStretch(1)
        scroll.setWidget(rail)
        return scroll

    def _build_brand_header(self):
        mono = pick_mono_font()
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(9)

        logo_pixmap = QPixmap(str(LOGO_PATH))
        if not logo_pixmap.isNull():
            logo_label = QLabel()
            logo_label.setPixmap(logo_pixmap.scaledToWidth(RAIL_WIDTH - 32, Qt.SmoothTransformation))
            logo_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(logo_label)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(0)
        tagline = QLabel("MeshCore node logger")
        tagline.setStyleSheet(f"font-family: '{mono}'; font-size: 12px; color: {DIM};")
        brand_row.addWidget(tagline)
        brand_row.addStretch(1)
        settings_btn = make_button("Settings", variant="secondary", height=26)
        settings_btn.setToolTip("Settings (Ctrl+,)")
        settings_btn.clicked.connect(self._open_settings_dialog)
        brand_row.addWidget(settings_btn)
        layout.addLayout(brand_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.device_arrow_label = QLabel("▸")
        self.device_arrow_label.setStyleSheet(f"font-family: '{mono}'; font-size: 12px; color: {DIM};")
        status_row.addWidget(self.device_arrow_label)
        self.status_label = QLabel("not connected")
        self.status_label.setStyleSheet(f"font-family: '{mono}'; font-size: 10px;")
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        self.live_pill = LivePill()
        pill_row = QHBoxLayout()
        pill_row.addWidget(self.live_pill)
        pill_row.addStretch(1)
        layout.addLayout(pill_row)

        # GPS fix is live status, not configuration -- shown here (rather
        # than only inside the settings dialog) so it stays visible/updating
        # while the dialog is closed. Only exists at all when GPS is
        # actually configured (see gps_configured in __init__).
        self.gps_fix_label = None
        if self.gps_configured:
            gps_block = QVBoxLayout()
            gps_block.setSpacing(2)
            gps_label = QLabel("GPS")
            gps_label.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
            gps_block.addWidget(gps_label)
            self.gps_fix_label = QLabel("not configured")
            self.gps_fix_label.setStyleSheet(f"font-family: '{mono}'; font-size: 15px; font-weight: 600; color: {DIM};")
            gps_block.addWidget(self.gps_fix_label)
            layout.addLayout(gps_block)

        divider_line = QFrame()
        divider_line.setFrameShape(QFrame.HLine)
        layout.addWidget(divider_line)

        return block

    # ---- wiring -----------------------------------------------------------

    def _wire_signals(self):
        self.connection_panel.refresh_clicked.connect(self._refresh_ports)
        self.connection_panel.connect_clicked.connect(self._toggle_connection)
        self.advertise_panel.zero_hop_clicked.connect(lambda: self._send_advert(False))
        self.advertise_panel.flood_clicked.connect(lambda: self._send_advert(True))
        self.sync_panel.send_wdgwars_clicked.connect(self._send_pending_wdgwars)
        self.sync_panel.send_ingest_clicked.connect(self._send_pending_ingest)
        self.sync_panel.clear_contacts_clicked.connect(self._clear_device_contacts)

        self.bridge.status.connect(self._on_status)
        self.bridge.error.connect(self._on_error)
        self.bridge.nodes.connect(self.node_panel.update_counts)
        self.bridge.ble_devices.connect(self._on_ble_devices)
        self.bridge.log.connect(self._log_line)
        self.bridge.wdgwars_sent.connect(self._on_wdgwars_sent)
        self.bridge.ingest_sent.connect(self._on_ingest_sent)

    # ---- settings dialog --------------------------------------------------

    def _open_settings_dialog(self):
        dialog = SettingsDialog(
            self.config_data,
            list_serial_ports(),
            self.gps_configured,
            self.ingest_configured,
            parent=self,
        )
        self._settings_dialog = dialog
        try:
            if dialog.exec() == QDialog.Accepted:
                self._apply_settings(dialog.values(self.config_data))
        finally:
            self._settings_dialog = None

    def _apply_settings(self, new_config):
        gps_port_before = self.config_data.get("gps_port", "")
        gps_enabled_before = self.config_data.get("gps_enabled", True)
        self.config_data = new_config
        save_config(self.config_data)
        self._log_line("Config saved")
        if self.worker is not None:
            self.worker.apply_config(self.config_data)
        self._update_send_pending_visibility()
        if (
            self.config_data.get("gps_port", "") != gps_port_before
            or self.config_data.get("gps_enabled", True) != gps_enabled_before
        ):
            self._start_gps_worker()

    # ---- terminal logging ---------------------------------------------------

    def _open_session_log(self, port):
        self._close_session_log()
        try:
            SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_port = Path(port).name or "device"
            path = SESSION_LOG_DIR / f"{ts}_{safe_port}.log"
            self.session_log_file = path.open("w", encoding="utf-8")
            self.session_log_path = path
        except OSError as exc:
            self.session_log_file = None
            self.session_log_path = None
            self._log_line(f"-- Could not open session log file: {exc} --")

    def _close_session_log(self):
        if self.session_log_file is not None:
            try:
                self.session_log_file.close()
            except OSError:
                pass
            self.session_log_file = None
            self.session_log_path = None

    def _log_line(self, line):
        self.terminal_log.append_line(line)
        if self.session_log_file is not None:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                self.session_log_file.write(f"[{ts}] {line}\n")
                self.session_log_file.flush()
            except OSError:
                self._close_session_log()

    # ---- bridge slots -------------------------------------------------------

    def _on_status(self, payload):
        self._log_line(f"-- {payload} --")
        connected = payload.startswith("Connected")
        if connected:
            self._update_brand_status(True, self.config_data["serial_device"])
            self.live_pill.set_connected(True)
        self._set_connected_controls_state(connected)
        if payload == "Disconnected":
            self._update_brand_status(False)
            self._close_session_log()
            self.live_pill.set_connected(False)

    def _on_error(self, payload):
        self._log_line(f"-- Error: {payload} --")
        self.connection_panel.set_connect_text("Connect")
        self._set_connected_controls_state(False)
        self._update_brand_status(False)
        self.live_pill.set_connected(False)
        self.worker = None
        self._close_session_log()

    def _on_ble_devices(self, payload):
        self._ble_entries = [format_ble_entry(name, addr) for name, addr in payload]
        self.connection_panel.set_devices(list_serial_ports() + self._ble_entries)
        # Always report completion, even at zero -- a scan that's actually
        # finished-but-empty (nothing in range, or a device that's already
        # connected elsewhere and so isn't advertising) is otherwise
        # silently indistinguishable from one that's still hung.
        self._log_line(f"-- Found {len(payload)} BLE device(s) --")

    def _on_wdgwars_sent(self, payload):
        self.wdgwars_sent_total += payload
        self.sync_panel.set_wdgwars_total(self.wdgwars_sent_total)

    def _on_ingest_sent(self, payload):
        self.ingest_sent_total += payload
        self.sync_panel.set_ingest_total(self.ingest_sent_total)

    # ---- actions ------------------------------------------------------------

    def _set_connected_controls_state(self, connected):
        self.sync_panel.set_clear_contacts_enabled(connected)
        self.advertise_panel.set_enabled(connected)

    def _update_brand_status(self, connected, port=None):
        mono = pick_mono_font()
        if connected and port:
            self.device_arrow_label.setStyleSheet(f"font-family: '{mono}'; font-size: 12px; color: {ACCENT};")
            kind, target = parse_device_selection(port)
            if kind == "ble":
                self.status_label.setText(f"BLE · {target}")
            else:
                self.status_label.setText(f"{target} · {SERIAL_BAUD}")
        else:
            self.device_arrow_label.setStyleSheet(f"font-family: '{mono}'; font-size: 12px; color: {DIM};")
            self.status_label.setText("not connected")

    def _refresh_ports(self):
        ports = list_serial_ports()
        self.connection_panel.set_devices(ports + self._ble_entries)
        if self._settings_dialog is not None:
            self._settings_dialog.gps_panel.set_ports(ports)
        self._scan_for_ble()

    def _scan_for_ble(self):
        self._log_line("-- Scanning for BLE devices... --")
        threading.Thread(target=scan_ble_devices_sync, args=(self.bridge,), daemon=True).start()

    def _start_gps_worker(self):
        if self.gps_worker is not None:
            self.gps_worker.stop()
            self.gps_worker = None
        if not self.config_data.get("gps_enabled", True):
            return
        port = self.config_data.get("gps_port", "").strip()
        if not port:
            return
        self.gps_worker = GPSWorker(port, self.gps_state, self.bridge)
        self.gps_worker.start()

    def _update_gps_display(self):
        if self.gps_fix_label is None:
            return
        mono = pick_mono_font()
        gps_style = "font-family: '{}'; font-size: 15px; font-weight: 600; color: {};"
        if not self.config_data.get("gps_enabled", True):
            self.gps_fix_label.setStyleSheet(gps_style.format(mono, DIM))
            self.gps_fix_label.setText("disabled")
        elif self.gps_worker is None:
            self.gps_fix_label.setStyleSheet(gps_style.format(mono, DIM))
            self.gps_fix_label.setText("not configured")
        else:
            fix = self.gps_state.fix
            if fix:
                lat, lon = fix
                self.gps_fix_label.setStyleSheet(gps_style.format(mono, ACCENT))
                self.gps_fix_label.setText(f"{lat:.5f}, {lon:.5f}")
            else:
                self.gps_fix_label.setStyleSheet(gps_style.format(mono, DIM))
                self.gps_fix_label.setText("waiting for fix...")

    def _toggle_connection(self):
        if self.worker is None:
            selection = self.connection_panel.device_selection()
            if not selection:
                self._log_line("Choose a serial device first")
                return
            self.config_data["serial_device"] = selection
            save_config(self.config_data)
            kind, target = parse_device_selection(selection)
            label = f"BLE {target}" if kind == "ble" else target
            self.connection_panel.set_connect_text("Disconnect")
            self.node_panel.reset()
            self.terminal_log.clear()
            self._open_session_log(f"ble_{target.replace(':', '-')}" if kind == "ble" else target)
            self._log_line(f"-- Connecting to {label}... --")
            if self.session_log_path is not None:
                self._log_line(f"-- Logging serial terminal session to {self.session_log_path} --")
            self.worker = MeshCoreWorker(
                target,
                self.bridge,
                connection_kind=kind,
                api_key=self.config_data["api_key"],
                auto_upload=self.config_data["auto_upload"],
                ingest_url=self.config_data["ingest_url"],
                ingest_api_key=self.config_data["ingest_api_key"],
                ingest_auto_send=self.config_data["ingest_auto_send"],
                auto_clear_contacts=self.config_data["auto_clear_contacts"],
                batch_uploads=self.config_data["batch_uploads"],
                gps_state=self.gps_state,
            )
            self.worker.start()
        else:
            self.worker.stop()
            self.connection_panel.set_connect_text("Connect")
            self._set_connected_controls_state(False)
            self._update_brand_status(False)
            self.live_pill.set_connected(False)
            self.worker = None

    def _clear_device_contacts(self):
        if self.worker is None:
            return
        if dialogs.confirm_clear_device_contacts(self):
            self.worker.clear_all_contacts()

    def _send_advert(self, flood):
        if self.worker is not None:
            self.worker.send_advert(flood)

    def _update_send_pending_visibility(self):
        batch_uploads = self.config_data.get("batch_uploads", False)
        self.sync_panel.set_send_wdgwars_visible(batch_uploads)
        self.sync_panel.set_send_ingest_visible(batch_uploads and self.ingest_configured)

    def _send_pending_wdgwars(self):
        self._log_line("-- Sending pending batch to WDGWars... --")
        threading.Thread(target=flush_pending_wdgwars, args=(self.config_data, self.bridge), daemon=True).start()

    def _send_pending_ingest(self):
        self._log_line("-- Sending pending batch to Ingest API... --")
        threading.Thread(target=flush_pending_ingest, args=(self.config_data, self.bridge), daemon=True).start()

    # ---- lifecycle ------------------------------------------------------

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.stop()
        if self.gps_worker is not None:
            self.gps_worker.stop()
        self._close_session_log()
        super().closeEvent(event)
