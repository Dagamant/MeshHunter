"""The main application window: owns the MeshCoreWorker/GPSWorker lifecycle,
the layout, and all the signal wiring between them.

Layout (desktop redesign 1a): top bar (device, LIVE, GPS, advertise, sync,
Settings) / stats strip / messages + packet inspector / RF band / serial
drawer. PocketMainWindow (pocket_window.py) builds its own compact layout
from the older panel widgets; both expose the same attribute names
(connection_panel, sync_panel, node_panel, terminal_log, ...) so the logic
here drives either.

MeshCoreWorker (core/worker.py) runs the MeshCore connection as a task on
the same qasync-merged event loop this window's own Qt event loop runs on
(see gui_qt/app.py), rather than on a separate thread. It just calls
ui_queue.put((kind, payload)); "ui_queue" is a Bridge (see bridge.py), so
those calls arrive as same-thread Qt signal deliveries.

GPSWorker stays a real OS thread (pyserial's reads are blocking, with no
asyncio-native equivalent) -- its Bridge signals still cross a real thread
boundary, which Qt's signal/slot connections handle safely regardless.

Configuration lives in a modal Settings dialog (see settings_dialog.py),
opened from the top bar's Settings button (or Ctrl+,).
"""

import asyncio
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import load_config, load_raw_config, save_config
from ..core.constants import SERIAL_BAUD
from ..core.devices import (
    format_ble_entry,
    list_devices,
    list_serial_ports,
    parse_device_selection,
    scan_ble_devices_sync,
)
from ..core.gps import GPSState, GPSWorker
from ..core.json_import import import_contacts_json
from ..core.logging_bridge import attach_library_logging
from ..core.lora_worker import LoRaWorker, radio_settings_from_config
from ..core.paths import LORA_PACKETS_CSV_PATH
from ..core.rtlsdr import WaterfallWorker
from ..core.session import SessionLog, device_label, make_worker, session_log_name
from ..core.uploads import flush_pending_ingest, flush_pending_wdgwars
from ..core.worker import MeshCoreWorker
from . import dialogs
from .bridge import Bridge
from .settings_dialog import SettingsDialog
from .theme import LINE, LOGO_PATH, PANEL_2
from .widgets.hat_panel import HatPanel
from .widgets.message_panel import MessagePanel
from .widgets.packet_inspector import OverlayShadow, PacketInspector
from .widgets.packet_table import PacketTable
from .widgets.sdr_waterfall import WaterfallView
from .widgets.serial_drawer import SerialDrawer
from .widgets.stats_strip import StatsStrip
from .widgets.top_bar import TopBar
from .widgets.waterfall import WaterfallPanel

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
    # Whether this layout includes the LoRa HAT mode switch/panels.
    # PocketMainWindow turns it off -- on a HAT device the fullscreen HAT
    # touch UI (hat_pocket_window.py) covers that instead.
    HAS_HAT_UI = True
    # Redesign 1c: at or below this width the window switches to its
    # small layout (see _set_small). PocketMainWindow turns it off.
    SMALL_BREAKPOINT = 1280
    SMALL_RF_HEIGHT = 140

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeshHunter")
        self.setWindowIcon(QIcon(str(LOGO_PATH)))
        self.resize(1440, 900)
        self.setMinimumSize(1024, 680)

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
        self.session_log = SessionLog()
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

        self.connection_panel.set_devices(list_devices() + self._ble_entries)
        self.connection_panel.set_device_selection(self.config_data.get("serial_device", ""))
        self._update_send_pending_visibility()
        if self.HAS_HAT_UI:
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
        # No menu bar; Settings is the top bar's button, but the keyboard
        # shortcut is still worth keeping.
        prefs_action = QAction("Preferences", self)
        prefs_action.setShortcut(QKeySequence("Ctrl+,"))
        prefs_action.triggered.connect(self._open_settings_dialog)
        self.addAction(prefs_action)

        serial_action = QAction("Toggle serial drawer", self)
        serial_action.setShortcut(QKeySequence("Ctrl+`"))
        serial_action.triggered.connect(self._toggle_serial_drawer)
        self.addAction(serial_action)

        close_inspector = QAction("Close packet inspector", self)
        close_inspector.setShortcut(QKeySequence(Qt.Key_Escape))
        close_inspector.triggered.connect(self._close_inspector)
        self.addAction(close_inspector)

    def _build_widgets(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.top_bar = TopBar()
        root.addWidget(self.top_bar)
        # Same names the rest of this class (and the pocket layout) use.
        self.connection_panel = self.top_bar.device_chip
        self.advertise_panel = self.top_bar.advertise
        self.sync_panel = self.top_bar.sent
        self.live_pill = self.top_bar.live
        self.sync_panel.set_ingest_shown(self.ingest_configured)
        self.connection_panel.set_modes(_MODES if HAT_SUPPORTED else _MODES[:1])

        self.stats_strip = StatsStrip()
        root.addWidget(self.stats_strip)
        self.node_panel = self.stats_strip
        self.sniffer_stats = self.stats_strip

        # Body: messages | inspector (companion/sniffer) or the HAT's
        # packet table, above the RF band and the serial drawer.
        self.message_panel = MessagePanel()
        self.inspector = PacketInspector()
        self.inspector.setVisible(False)
        messages_page = QWidget()
        self.messages_page = messages_page
        self.inspector_shadow = OverlayShadow(messages_page)
        self.inspector_shadow.setVisible(False)
        self._small = False
        mrow = QHBoxLayout(messages_page)
        self._messages_row = mrow
        mrow.setContentsMargins(0, 0, 0, 0)
        mrow.setSpacing(0)
        mrow.addWidget(self.message_panel, stretch=1)
        mrow.addWidget(self.inspector)
        self.packet_table = PacketTable()
        self.body_stack = QStackedWidget()
        self.body_stack.addWidget(messages_page)
        self.body_stack.addWidget(self.packet_table)

        ui = self.config_data
        self.waterfall_panel = WaterfallPanel(graph_width=ui.get("ui_rf_graph_width"), gain_db=ui.get("sdr_gain"))
        self.waterfall_view = WaterfallView()  # LoRa HAT's own RTL-SDR view
        self.rf_stack = QStackedWidget()
        self.rf_stack.addWidget(self.waterfall_panel)
        self.rf_stack.addWidget(self.waterfall_view)
        self.rf_stack.setMinimumHeight(140)
        self.rf_stack.setVisible(False)  # until a sniffer (or the HAT) is live

        self.serial_drawer = SerialDrawer()
        self.terminal_log = self.serial_drawer

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        self.splitter.addWidget(self.body_stack)
        self.splitter.addWidget(self.rf_stack)
        self.splitter.addWidget(self.serial_drawer)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setStretchFactor(2, 0)
        self.body_stack.setMinimumHeight(160)
        root.addWidget(self.splitter, stretch=1)

        # HAT controls open as a popover anchored to the device chip.
        self.hat_popover = QFrame(self, Qt.Popup)
        self.hat_popover.setObjectName("hatPopover")
        self.hat_popover.setStyleSheet(f"#hatPopover {{ background: {PANEL_2}; border: 1px solid {LINE};"
                                       f" border-radius: 10px; }}")
        pop = QVBoxLayout(self.hat_popover)
        pop.setContentsMargins(4, 4, 4, 4)
        self.hat_panel = HatPanel()
        pop.addWidget(self.hat_panel)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(lambda: save_config(self.config_data))
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        if ui.get("ui_serial_open"):
            self.serial_drawer.set_open(True)
        self._apply_splitter_sizes()

    # ---- layout state -------------------------------------------------------

    def _apply_splitter_sizes(self):
        ui = self.config_data
        total = max(self.splitter.height(), 600)
        rf_height = self.SMALL_RF_HEIGHT if self._small else ui.get("ui_rf_height", 230)
        rf = rf_height if self.rf_stack.isVisible() else 0
        serial = ui.get("ui_serial_height", 240) if self.serial_drawer.is_open() else 30
        self.splitter.setSizes([max(160, total - rf - serial), rf, serial])

    def _on_splitter_moved(self, *_):
        sizes = self.splitter.sizes()
        if self.rf_stack.isVisible() and sizes[1] > 0 and not self._small:
            self.config_data["ui_rf_height"] = sizes[1]
        if self.serial_drawer.is_open():
            self.config_data["ui_serial_height"] = sizes[2]
        self._save_timer.start()

    # ---- small window (redesign 1c) ------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.SMALL_BREAKPOINT:
            small = self.width() <= self.SMALL_BREAKPOINT
            if small != self._small:
                self._set_small(small)

    def _set_small(self, small):
        self._small = small
        self.top_bar.set_compact(small)
        self.stats_strip.set_compact(small)
        self.message_panel.set_small(small)
        self.waterfall_panel.set_small(small)
        # The inspector floats over the table instead of taking a column.
        visible = self.inspector.isVisible()
        if small:
            self._messages_row.removeWidget(self.inspector)
            self.messages_page.installEventFilter(self)
        else:
            self.messages_page.removeEventFilter(self)
            self._messages_row.addWidget(self.inspector)
        self.inspector.set_overlay(small)
        self.inspector.setVisible(visible)
        self._place_inspector()
        self._apply_splitter_sizes()

    def _place_inspector(self):
        if self._small:
            page = self.messages_page
            inset = 8
            top = self.message_panel._header.height() + inset  # keep the filter / ⋯ menu usable
            self.inspector.setGeometry(page.width() - self.inspector.width() - inset, top,
                                       self.inspector.width(), max(120, page.height() - top - inset))
            self.inspector_shadow.follow(self.inspector.geometry())
            self.inspector_shadow.setVisible(self.inspector.isVisible())
            self.inspector_shadow.raise_()
            self.inspector.raise_()
        else:
            self.inspector_shadow.setVisible(False)

    def eventFilter(self, obj, event):
        if obj is self.messages_page and event.type() == QEvent.Resize:
            self._place_inspector()
        return super().eventFilter(obj, event)

    def _remember(self, key, value):
        self.config_data[key] = value
        self._save_timer.start()

    def _toggle_serial_drawer(self):
        self.serial_drawer.toggle()

    def _on_serial_toggled(self, open_):
        self._remember("ui_serial_open", open_)
        self._apply_splitter_sizes()

    # ---- packet inspector -------------------------------------------------

    def _noise_floor(self):
        noises = [p["noise"] for p in self.stats_strip._latest.values() if p.get("noise") is not None]
        return min(noises) if noises else None

    def _on_message_selected(self, m):
        if m is None:
            self.inspector.setVisible(False)
            self.inspector_shadow.setVisible(False)
            return
        self.inspector.show_message(m, self._noise_floor())
        self.inspector.setVisible(True)
        self._place_inspector()

    def _on_message_updated(self, m):
        if self.inspector.isVisible() and self.inspector.current() is m:
            self.inspector.refresh(m)

    def _close_inspector(self):
        if self.inspector.isVisible():
            self.inspector.setVisible(False)
            self.inspector_shadow.setVisible(False)
            self.message_panel.clear_selection()

    def _show_hat_popover(self):
        chip = self.top_bar.device_chip
        self.hat_popover.adjustSize()
        self.hat_popover.move(chip.mapToGlobal(QPoint(0, chip.height() + 6)))
        self.hat_popover.show()

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
        self._wire_layout_signals()
        if self.HAS_HAT_UI:
            self.hat_panel.start_clicked.connect(self._toggle_hat)
            self.hat_panel.settings_changed.connect(self._on_hat_settings_changed)
            self.waterfall_view.sdr_settings_changed.connect(self._on_sdr_settings_changed)

        self.bridge.status.connect(self._on_status)
        self.bridge.error.connect(self._on_error)
        self.bridge.nodes.connect(self.node_panel.update_counts)
        self.bridge.ble_devices.connect(self._on_ble_devices)
        self.bridge.log.connect(self._log_line)
        self.bridge.sniffer_profiles.connect(self._on_sniffer_profiles)
        self.bridge.sniffer_rssi.connect(self.waterfall_panel.add_rssi)
        self.bridge.sniffer_event.connect(self.waterfall_panel.add_event)
        self.bridge.sniffer_stat.connect(self.waterfall_panel.add_stat)
        self.bridge.sniffer_stat.connect(self._on_sniffer_stat)
        self.bridge.message.connect(self.message_panel.add_message)
        self.message_panel.channels_found.connect(self._add_found_channels)
        self._update_channel_filter()
        self.bridge.sniffer_radios.connect(self.waterfall_panel.set_radios)
        self.waterfall_panel.log.connect(self._log_line)
        self.bridge.wdgwars_sent.connect(self._on_wdgwars_sent)
        self.bridge.ingest_sent.connect(self._on_ingest_sent)
        if self.HAS_HAT_UI:
            self.bridge.hat_packet.connect(self._on_hat_packet)
            self.bridge.hat_status.connect(self._on_hat_status)
            self.bridge.hat_error.connect(self._on_hat_error)
            self.bridge.waterfall_row.connect(self.waterfall_view.canvas.add_row)
            self.bridge.waterfall_error.connect(self._on_waterfall_error)

    def _wire_layout_signals(self):
        """Signals of the desktop layout's own widgets (overridden to a
        no-op by PocketMainWindow)."""
        self.top_bar.settings_clicked.connect(self._open_settings_dialog)
        self.connection_panel.mode_selected.connect(self._set_mode)
        self.connection_panel.hat_settings_clicked.connect(self._show_hat_popover)
        self.message_panel.message_selected.connect(self._on_message_selected)
        self.message_panel.message_updated.connect(self._on_message_updated)
        self.inspector.closed.connect(self._close_inspector)
        self.serial_drawer.toggled.connect(self._on_serial_toggled)
        self.waterfall_panel.graph_width_changed.connect(lambda w: self._remember("ui_rf_graph_width", w))
        self.waterfall_panel.gain_changed.connect(lambda g: self._remember("sdr_gain", g))

    # ---- settings dialog --------------------------------------------------

    def _settings_dialog_size(self):
        """(width, height) to force the Settings dialog to, or None to let
        it size itself (Qt's default sizeHint-based behavior). Overridden
        by PocketMainWindow, which runs on a fixed small display where the
        dialog's natural size wouldn't fit on screen at all."""
        return None

    def _open_settings_dialog(self):
        counts, unknown = self.message_panel.channel_stats()
        dialog = SettingsDialog(
            self.config_data,
            list_serial_ports(),
            self.gps_configured,
            self.ingest_configured,
            parent=self,
            channel_counts=counts,
            unknown_hashes=unknown,
            find_channels=lambda entries: self.message_panel.crack_channels(entries, parent=dialog),
            sent={"wdgwars": self.wdgwars_sent_total, "ingest": self.ingest_sent_total},
        )
        size = self._settings_dialog_size()
        if size is not None:
            dialog.fit_small(*size)
        self._settings_dialog = dialog
        try:
            if dialog.exec() == QDialog.Accepted:
                self._apply_settings(dialog.values(self.config_data))
        finally:
            self._settings_dialog = None

    def _update_channel_filter(self):
        self.message_panel.set_subscribed(self.config_data.get("meshcore_channels") or [])

    def _add_found_channels(self, names):
        """Subscribe to channels recovered by "Find channels" (names derive
        their own key), skipping any already listed, then save and apply."""
        from ..core.channels import clean_entries

        entries = list(self.config_data.get("meshcore_channels") or [])
        have = {e["name"].lower() for e in entries}
        added = [n for n in names if n.lower() not in have and n.lower() != "public"]
        if not added:
            return
        entries += [{"name": n, "enabled": True} for n in added]
        self._apply_settings({**self.config_data, "meshcore_channels": clean_entries(entries)})
        self._log_line(f"Subscribed to {len(added)} recovered channel(s): {', '.join(added)}")

    def _apply_settings(self, new_config):
        gps_port_before = self.config_data.get("gps_port", "")
        gps_enabled_before = self.config_data.get("gps_enabled", True)
        self.config_data = new_config
        save_config(self.config_data)
        self._log_line("Config saved")
        if self.worker is not None:
            self.worker.apply_config(dict(self.config_data))
        if self._hat_recorder is not None:
            self._hat_recorder.apply_config(self.config_data)
        self._update_channel_filter()
        self._update_send_pending_visibility()
        if (
            self.config_data.get("gps_port", "") != gps_port_before
            or self.config_data.get("gps_enabled", True) != gps_enabled_before
        ):
            self._start_gps_worker()

    # ---- terminal logging ---------------------------------------------------

    def _open_session_log(self, port):
        error = self.session_log.open(port)
        if error:
            self._log_line(error)

    def _close_session_log(self):
        self.session_log.close()

    def _log_line(self, line):
        self.terminal_log.append_line(line)
        self.session_log.write(line)

    # ---- bridge slots -------------------------------------------------------

    def _on_status(self, payload):
        self._log_line(f"-- {payload} --")
        connected = payload.startswith("Connected")
        if connected:
            self._update_brand_status(True, self.config_data["serial_device"])
            self.live_pill.set_connected(True)
            self._session_timer(True)
        self._set_connected_controls_state(connected)
        if payload == "Disconnected":
            self.waterfall_panel.set_live(False)  # frees the RTL-SDR
            self._update_brand_status(False)
            self._close_session_log()
            self.live_pill.set_connected(False)
            self._session_timer(False)

    def _on_error(self, payload):
        self._log_line(f"-- Error: {payload} --")
        self.connection_panel.set_connect_text("Connect")
        self._set_connected_controls_state(False)
        self._update_brand_status(False)
        self.live_pill.set_connected(False)
        self.worker = None
        self.waterfall_panel.set_live(False)
        self._close_session_log()
        self._session_timer(False)

    def _on_ble_devices(self, payload):
        self._ble_entries = [format_ble_entry(name, addr) for name, addr in payload]
        self.connection_panel.set_devices(list_devices() + self._ble_entries)
        # Always report completion, even at zero -- a scan that's actually
        # finished-but-empty (nothing in range, or a device that's already
        # connected elsewhere and so isn't advertising) is otherwise
        # silently indistinguishable from one that's still hung.
        self._log_line(f"-- Found {len(payload)} BLE device(s) --")

    def _on_sniffer_profiles(self, payload):
        self.waterfall_panel.set_profiles(payload)
        self._set_waterfall_visible(True)

    def _on_sniffer_stat(self, payload):
        bar = getattr(self, "sniffer_stats", None)
        if bar is not None:
            bar.add_stat(payload)

    def _set_waterfall_visible(self, visible):
        """Show/hide the RF band. Overridden by PocketMainWindow, where the
        waterfall is a tab."""
        if self.config_data.get("mode") == "hat" and self.HAS_HAT_UI:
            visible = True  # the HAT's own waterfall lives there
        if visible != self.rf_stack.isVisible():
            self.rf_stack.setVisible(visible)
            self._apply_splitter_sizes()

    def _session_timer(self, running):
        """Overridden by PocketMainWindow (no stats strip there)."""
        if running:
            self.stats_strip.start_session()
        else:
            self.stats_strip.stop_session()

    def _on_wdgwars_sent(self, payload):
        self.wdgwars_sent_total += payload
        self.sync_panel.set_wdgwars_total(self.wdgwars_sent_total)

    def _on_ingest_sent(self, payload):
        self.ingest_sent_total += payload
        self.sync_panel.set_ingest_total(self.ingest_sent_total)

    # ---- actions ------------------------------------------------------------

    def _set_connected_controls_state(self, connected):
        # The sniffer is receive-only with no contact table, so advert and
        # clear-contacts stay disabled while one is connected.
        device_commands = connected and not getattr(self.worker, "is_sniffer", False)
        self.sync_panel.set_clear_contacts_enabled(device_commands)
        self.advertise_panel.set_enabled(device_commands)

    def _update_brand_status(self, connected, port=None):
        """Device chip text. Overridden by PocketMainWindow (status label)."""
        if connected and port:
            kind, target = parse_device_selection(port)
            if kind == "ble":
                text, detail = target, "BLE"
            elif kind == "usb_radio":
                text, detail = target or "CH341", "USB LoRa"
            elif kind == "sniffer_group":
                text, detail = "All LoRa sniffers", "Hop" if self.config_data.get("sniffer_mode") == "hop" else "Fixed"
            elif getattr(self.worker, "is_sniffer", False):
                text, detail = target, "LoRa sniffer"
            else:
                text, detail = target, f"Companion · {SERIAL_BAUD}"
            self.connection_panel.set_status(True, text, detail)
        else:
            self.connection_panel.set_status(False)

    def _refresh_ports(self):
        ports = list_serial_ports()
        self.connection_panel.set_devices(list_devices() + self._ble_entries)
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
        """Top bar GPS indicator. Overridden by PocketMainWindow."""
        if not self.gps_configured:
            self.top_bar.set_gps("hidden")
        elif not self.config_data.get("gps_enabled", True):
            self.top_bar.set_gps("off", "disabled")
        elif self.gps_worker is None:
            self.top_bar.set_gps("off", "no port set")
        else:
            fix = self.gps_state.fix
            if fix:
                self.top_bar.set_gps("fix", f"{fix[0]:.4f}, {fix[1]:.4f}")
            else:
                self.top_bar.set_gps("nofix")

    def _toggle_connection(self):
        if self.worker is None:
            selection = self.connection_panel.device_selection()
            if not selection:
                self._log_line("Choose a serial device first")
                return
            self.config_data["serial_device"] = selection
            save_config(self.config_data)
            self.connection_panel.set_connect_text("Disconnect")
            self.node_panel.reset()
            self.terminal_log.clear()
            self.message_panel.clear()
            self.waterfall_panel.clear()
            if getattr(self, "sniffer_stats", None) is not None:
                self.sniffer_stats.clear()
            self._set_waterfall_visible(False)
            self._open_session_log(session_log_name(selection))
            self._log_line(f"-- Connecting to {device_label(selection)}... --")
            if self.session_log.path is not None:
                self._log_line(f"-- Logging serial terminal session to {self.session_log.path} --")
            self.worker = make_worker(selection, self.config_data, self.bridge, self.gps_state)
            if getattr(self.worker, "is_sniffer", False) and len(self.worker.ports) == 1:
                self._log_line(f"-- {device_label(selection)} is a MeshHunter LoRa sniffer --")
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
        self.connection_panel.set_mode(mode)
        self.top_bar.set_hat_mode(hat)
        self.body_stack.setCurrentIndex(1 if hat else 0)
        self.rf_stack.setCurrentIndex(1 if hat else 0)
        if self.config_data.get("mode") != mode:
            self.config_data["mode"] = mode
            save_config(self.config_data)
        if hat:
            self._set_waterfall_visible(True)
        else:
            self._stop_hat()
            self.hat_popover.hide()
            self._set_waterfall_visible(self.waterfall_panel._live)

    def _toggle_hat(self):
        if self.lora_worker is None:
            self._start_hat()
        else:
            self._stop_hat()

    def _start_hat(self):
        settings = radio_settings_from_config(self.config_data)
        if self.session_log.file is None:
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
        self.connection_panel.set_status(True, "LoRa HAT", settings.title)

    def _start_waterfall(self, settings):
        # The radio panel's sniffer waterfall may be using the same dongle.
        self.waterfall_panel.set_sdr_allowed(False)
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
        if not self.HAS_HAT_UI:
            return
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
        self.waterfall_panel.set_sdr_allowed(True)
        self.hat_panel.set_running(False)
        self.packet_table.set_status("stopped")
        if self.worker is None:
            self.live_pill.set_connected(False)
            self.connection_panel.set_status(False)

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
        self.waterfall_panel.shutdown()
        self._stop_hat()
        if self.worker is not None:
            self.worker.stop()
        if self.gps_worker is not None:
            self.gps_worker.stop()
        self._close_session_log()
        super().closeEvent(event)
