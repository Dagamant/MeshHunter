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

import asyncio
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QComboBox,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import load_config, load_raw_config, save_config
from ..core.constants import SERIAL_BAUD
from ..core.devices import format_ble_entry, list_serial_ports, parse_device_selection, scan_ble_devices_sync
from ..core.gps import GPSState, GPSWorker
from ..core.json_import import import_contacts_json
from ..core.logging_bridge import attach_library_logging
from ..core.lora_worker import LoRaWorker, radio_settings_from_config
from ..core.paths import LORA_PACKETS_CSV_PATH, SESSION_LOG_DIR
from ..core.rtlsdr import WaterfallWorker
from ..core.uploads import flush_pending_ingest, flush_pending_wdgwars
from ..core.worker import MeshCoreWorker
from . import dialogs
from .bridge import Bridge
from .settings_dialog import SettingsDialog
from .theme import ACCENT, DIM, LOGO_PATH, RAIL_WIDTH, pick_mono_font
from .widgets.advertise_panel import AdvertisePanel
from .widgets.common import make_button
from .widgets.common import section_label
from .widgets.connection_panel import ConnectionPanel
from .widgets.hat_panel import HatPanel
from .widgets.live_pill import LivePill
from .widgets.node_panel import NodePanel
from .widgets.packet_table import PacketTable
from .widgets.sync_panel import SyncPanel
from .widgets.terminal_log import TerminalLog
from .widgets.waterfall import WaterfallView

# Optional/power-user config keys -- not part of example-config.json's
# standard set, so their settings-dialog sections (and this main-window GPS
# readout) only appear once the user has hand-added the relevant key(s) to
# config.json at least once.
_GPS_KEYS = ("gps_port", "gps_enabled")
_INGEST_KEYS = ("ingest_url", "ingest_api_key", "ingest_auto_send")

# The LoRa HAT (SX1262 on SPI + RTL-SDR) is driven through Linux spidev/
# gpiod, so its mode is only offered there.
HAT_SUPPORTED = sys.platform.startswith("linux")
_MODES = (("companion", "Companion device"), ("hat", "LoRa HAT"))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeshHunter")
        self.setWindowIcon(QIcon(str(LOGO_PATH)))
        self.resize(1280, 860)
        self.setMinimumSize(760, 560)

        self.config_data = load_config()
        if self.config_data["lora_network"] not in ("MeshCore", "Meshtastic"):
            self.config_data["lora_network"] = "MeshCore"  # e.g. the touch UI's "Custom"
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

        # LoRa HAT: receiver + waterfall threads, and a device-less
        # MeshCoreWorker used only to log adverts the HAT hears into the
        # same node CSVs a companion device would.
        self.lora_worker = None
        self.waterfall_worker = None
        self._hat_recorder = None

        self._build_shortcuts()
        self._build_widgets()
        self._wire_signals()

        self.connection_panel.set_devices(list_serial_ports() + self._ble_entries)
        self.connection_panel.set_device_selection(self.config_data.get("serial_device", ""))
        self._update_send_pending_visibility()
        self.hat_panel.set_values(self.config_data)
        self.waterfall_view.set_sdr_values(self.config_data["sdr_sample_rate"], self.config_data["sdr_gain"])
        self._set_mode(self.config_data.get("mode", "companion") if HAT_SUPPORTED else "companion")

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

        # Companion mode shows the serial terminal; LoRa HAT mode splits
        # the same space between decoded packets and the SDR waterfall.
        self.content_stack = QStackedWidget()
        self.terminal_log = TerminalLog()
        self.content_stack.addWidget(self.terminal_log)
        self.hat_splitter = QSplitter(Qt.Vertical)
        self.hat_splitter.setChildrenCollapsible(False)
        self.packet_table = PacketTable()
        self.waterfall_view = WaterfallView()
        self.hat_splitter.addWidget(self.packet_table)
        self.hat_splitter.addWidget(self.waterfall_view)
        self.hat_splitter.setSizes([1, 1])
        self.content_stack.addWidget(self.hat_splitter)
        content_layout.addWidget(self.content_stack, stretch=1)

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

        self.mode_block = QWidget()
        mode_layout = QVBoxLayout(self.mode_block)
        mode_layout.setContentsMargins(16, 14, 16, 0)
        mode_layout.setSpacing(11)
        mode_layout.addWidget(section_label("MODE"))
        self.mode_combo = QComboBox()
        self.mode_combo.setMinimumHeight(34)
        for key, label in _MODES:
            self.mode_combo.addItem(label, key)
        mode_layout.addWidget(self.mode_combo)
        self.mode_block.setVisible(HAT_SUPPORTED)
        layout.addWidget(self.mode_block)

        self.hat_panel = HatPanel()
        layout.addWidget(self.hat_panel)

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
        self.sync_panel.import_json_clicked.connect(self._import_contacts_json)
        self.mode_combo.currentIndexChanged.connect(lambda _: self._set_mode(self.mode_combo.currentData()))
        self.hat_panel.start_clicked.connect(self._toggle_hat)
        self.hat_panel.settings_changed.connect(self._on_hat_settings_changed)
        self.waterfall_view.sdr_settings_changed.connect(self._on_sdr_settings_changed)

        self.bridge.status.connect(self._on_status)
        self.bridge.error.connect(self._on_error)
        self.bridge.nodes.connect(self.node_panel.update_counts)
        self.bridge.ble_devices.connect(self._on_ble_devices)
        self.bridge.log.connect(self._log_line)
        self.bridge.wdgwars_sent.connect(self._on_wdgwars_sent)
        self.bridge.ingest_sent.connect(self._on_ingest_sent)
        self.bridge.hat_packet.connect(self._on_hat_packet)
        self.bridge.hat_status.connect(self._on_hat_status)
        self.bridge.hat_error.connect(self._on_hat_error)
        self.bridge.waterfall_row.connect(self.waterfall_view.canvas.add_row)
        self.bridge.waterfall_error.connect(self._on_waterfall_error)

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
        if self._hat_recorder is not None:
            self._hat_recorder.apply_config(self.config_data)
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

    def _import_contacts_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import contacts from JSON", "", "JSON files (*.json)")
        if not path:
            return
        self._log_line(f"-- Importing contacts from {Path(path).name}... --")
        threading.Thread(target=import_contacts_json, args=(path, self.bridge), daemon=True).start()

    # ---- LoRa HAT -----------------------------------------------------------

    def _set_mode(self, mode):
        hat = mode == "hat"
        index = next(i for i, (key, _) in enumerate(_MODES) if key == mode)
        if self.mode_combo.currentIndex() != index:
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(index)
            self.mode_combo.blockSignals(False)
        self.hat_panel.setVisible(hat)
        self.connection_panel.setVisible(not hat)
        self.advertise_panel.setVisible(not hat)
        self.content_stack.setCurrentIndex(1 if hat else 0)
        if not hat:
            self._stop_hat()
        if self.config_data.get("mode") != mode:
            self.config_data["mode"] = mode
            save_config(self.config_data)

    def _toggle_hat(self):
        if self.lora_worker is None:
            self._start_hat()
        else:
            self._stop_hat()

    def _start_hat(self):
        settings = radio_settings_from_config(self.config_data)
        if self.session_log_file is None:
            self._open_session_log("lora_hat")
        self._log_line(f"-- Starting LoRa HAT: {settings.label} --")
        self._log_line(f"-- Logging every received packet to {LORA_PACKETS_CSV_PATH} --")
        if self._hat_recorder is None:
            self._hat_recorder = MeshCoreWorker(None, self.bridge, gps_state=self.gps_state)
            self._hat_recorder.apply_config(self.config_data)
        self._hat_recorder.load_known_nodes()

        self.lora_worker = LoRaWorker(settings, self.bridge, self.gps_state)
        self.lora_worker.start()
        self._start_waterfall(settings)
        self.hat_panel.set_running(True)
        self.packet_table.set_status(f"starting · {settings.label}", active=True)
        self.live_pill.set_connected(True)

    def _start_waterfall(self, settings):
        if self.waterfall_worker is not None:
            self.waterfall_worker.stop()
            self.waterfall_worker.join(timeout=2)
        center_hz = int(settings.freq_mhz * 1e6)
        sample_rate = self.waterfall_view.sample_rate()
        self.waterfall_view.canvas.configure(center_hz, sample_rate, settings.bw_khz * 1e3)
        self.waterfall_worker = WaterfallWorker(
            center_hz,
            sample_rate,
            on_row=self.bridge.waterfall_row.emit,
            on_error=self.bridge.waterfall_error.emit,
            gain_db=self.waterfall_view.gain_db(),
        )
        self.waterfall_worker.start()
        self.waterfall_view.set_status(f"RTL-SDR · {settings.freq_mhz:.3f} MHz ± {sample_rate / 2e6:.1f}", active=True)

    def _stop_hat(self):
        # Joined (briefly -- each loop iteration is <= 0.5 s) rather than
        # fire-and-forget: a retune starts a new worker straight after, and
        # it can't open the SX1262 GPIO lines / RTL-SDR until the old one
        # has let go of them.
        if self.lora_worker is not None:
            self.lora_worker.stop()
            self.lora_worker.join(timeout=2)
            self.lora_worker = None
        if self.waterfall_worker is not None:
            self.waterfall_worker.stop()
            self.waterfall_worker.join(timeout=2)
            self.waterfall_worker = None
            self.waterfall_view.set_status("RTL-SDR idle")
        self.hat_panel.set_running(False)
        self.packet_table.set_status("stopped")
        if self.worker is None:
            self.live_pill.set_connected(False)

    def _on_hat_settings_changed(self):
        self.config_data.update(self.hat_panel.values())
        save_config(self.config_data)
        if self.lora_worker is not None:
            # The SX1262 can only listen on one network/frequency at a
            # time, so a settings change means a fresh receiver.
            self._stop_hat()
            self._start_hat()

    def _on_sdr_settings_changed(self):
        self.config_data["sdr_sample_rate"] = self.waterfall_view.sample_rate()
        self.config_data["sdr_gain"] = self.waterfall_view.gain_db()
        save_config(self.config_data)
        if self.waterfall_worker is not None:
            self._start_waterfall(radio_settings_from_config(self.config_data))

    def _on_hat_packet(self, ev):
        self.packet_table.add_event(ev)
        p = ev.packet
        signal = f"rssi={ev.rssi:.0f} snr={ev.snr:+.1f}"
        self._log_line(f"[{ev.time:%H:%M:%S}] RX {p.network} {p.kind}: {signal} from={p.sender!r} {p.text} ({p.hops})")
        if ev.crc_ok and p.network == "MeshCore" and p.kind == "ADVERT" and p.node:
            # Prefer the live companion worker when one is connected, so
            # both paths share one in-memory copy of the node CSVs.
            recorder = self.worker if self.worker is not None and self.worker.meshcore is not None else self._hat_recorder
            if recorder is not None:
                asyncio.ensure_future(recorder.record_advert(p.node))

    def _on_hat_status(self, payload):
        self._log_line(f"-- LoRa HAT: {payload} --")
        # "Stopped" can arrive (queued) after a retune has already started
        # the next worker, so it's only logged, not shown as current status.
        if self.lora_worker is not None and payload != "Stopped":
            self.packet_table.set_status(payload, active=True)

    def _on_hat_error(self, payload):
        self._log_line(f"-- LoRa HAT error: {payload} --")
        self.packet_table.set_status(f"error: {payload}")
        if self.lora_worker is not None:
            self.lora_worker = None
            self.hat_panel.set_running(False)

    def _on_waterfall_error(self, payload):
        self._log_line(f"-- RTL-SDR error: {payload} --")
        self.waterfall_view.set_status(f"RTL-SDR error: {payload}")
        self.waterfall_worker = None

    # ---- lifecycle ------------------------------------------------------

    def closeEvent(self, event):
        self._stop_hat()
        if self.worker is not None:
            self.worker.stop()
        if self.gps_worker is not None:
            self.gps_worker.stop()
        self._close_session_log()
        super().closeEvent(event)
