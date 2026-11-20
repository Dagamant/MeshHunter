"""PocketMainWindow: a compact, touch-friendly layout for a fixed
640x480 display (the "PocketTerm35" target), reusing all of MainWindow's
business logic -- MeshCoreWorker/GPS lifecycle, Bridge wiring, settings
persistence, every _on_.../_send_.../_toggle_... handler -- unchanged.
See MainWindow's own docstring for what that shared logic does; only the
widget layout differs here:

- No side rail (380px alone wouldn't leave enough width to work with) and
  no full-size logo -- a slim top bar instead, with brand/status/live
  indicator/Settings.
- Tabs instead of everything visible at once: Home (connect + node
  counts -- the priority screen), Terminal (the serial log), Messages
  (every decoded packet, MeshCore and Meshtastic), Actions
  (advertise/sync/import/clear), and Radio (the LoRa sniffer waterfall,
  only while a sniffer is connected).
- Buttons/tabs sized for a finger, not a mouse pointer.

Toggled on via `--pocket` on the command line (see gui_qt/app.py) rather
than replacing MainWindow outright, since most users still want the
regular desktop layout.
"""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget

from ..core.constants import SERIAL_BAUD
from ..core.devices import parse_device_selection
from .main_window import MainWindow
from .theme import ACCENT, DIM, pick_mono_font
from .widgets.advertise_panel import AdvertisePanel
from .widgets.common import make_button
from .widgets.connection_panel import ConnectionPanel
from .widgets.live_pill import LivePill
from .widgets.message_panel import MessagePanel
from .widgets.node_panel import NodePanel
from .widgets.sync_panel import SyncPanel
from .widgets.terminal_log import TerminalLog
from .widgets.waterfall import WaterfallPanel

POCKET_WIDTH = 640
POCKET_HEIGHT = 480


class PocketMainWindow(MainWindow):
    # Companion-only: on a HAT device the fullscreen HAT touch UI
    # (hat_pocket_window.py) is used instead.
    HAS_HAT_UI = False

    def __init__(self):
        super().__init__()
        # Overrides MainWindow's resize(1280, 860)/setMinimumSize(760, 560)
        # -- this window's target is a fixed physical panel, not a resizable
        # desktop window, so min/max/current all pin to the same size.
        self.setFixedSize(POCKET_WIDTH, POCKET_HEIGHT)

    # ---- layout overrides -------------------------------------------------

    def _build_widgets(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_top_bar())
        root.addWidget(self._build_tabs(), stretch=1)

    def _build_top_bar(self):
        mono = pick_mono_font()
        bar = QWidget()
        bar.setFixedHeight(40)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(6)

        name = QLabel("MESHHUNTER")
        name.setStyleSheet(f"font-family: '{mono}'; font-size: 13px; font-weight: bold; color: {ACCENT};")
        layout.addWidget(name)

        self.device_arrow_label = QLabel("▸")
        self.device_arrow_label.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; color: {DIM};")
        layout.addWidget(self.device_arrow_label)

        self.status_label = QLabel("not connected")
        self.status_label.setStyleSheet(f"font-family: '{mono}'; font-size: 10px;")
        layout.addWidget(self.status_label)

        layout.addStretch(1)

        self.live_pill = LivePill()
        layout.addWidget(self.live_pill)

        # GPS fix isn't shown in the pocket top bar -- there's no room next
        # to brand/status/live without crowding all of them. _update_gps_display
        # (inherited from MainWindow) already no-ops whenever this is None.
        self.gps_fix_label = None

        settings_btn = make_button("Settings", variant="secondary", height=28)
        settings_btn.clicked.connect(self._open_settings_dialog)
        layout.addWidget(settings_btn)

        return bar

    def _build_tabs(self):
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        self.connection_panel = ConnectionPanel()
        self.node_panel = NodePanel()
        home = QWidget()
        home_layout = QVBoxLayout(home)
        home_layout.setContentsMargins(0, 0, 0, 0)
        home_layout.setSpacing(0)
        home_layout.addWidget(self.connection_panel)
        home_layout.addWidget(self.node_panel)
        home_layout.addStretch(1)
        tabs.addTab(home, "Home")

        self.terminal_log = TerminalLog()
        tabs.addTab(self.terminal_log, "Terminal")

        self.message_panel = MessagePanel(compact=True)
        tabs.addTab(self.message_panel, "Messages")

        self.advertise_panel = AdvertisePanel()
        self.sync_panel = SyncPanel()
        actions = QWidget()
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(0)
        actions_layout.addWidget(self.advertise_panel)
        actions_layout.addWidget(self.sync_panel)
        actions_layout.addStretch(1)
        tabs.addTab(actions, "Actions")

        # LoRa sniffer waterfall: a tab that only exists while one is connected.
        self.waterfall_panel = WaterfallPanel()
        self._radio_tab_index = tabs.addTab(self.waterfall_panel, "Radio")
        tabs.setTabVisible(self._radio_tab_index, False)
        self._tabs = tabs

        return tabs

    def _set_waterfall_visible(self, visible):
        self._tabs.setTabVisible(self._radio_tab_index, visible)

    # ---- the desktop layout's extras don't exist here -------------------------

    def _wire_layout_signals(self):
        pass

    def _session_timer(self, running):
        pass

    def _toggle_serial_drawer(self):
        pass

    def _close_inspector(self):
        pass

    def _apply_splitter_sizes(self):
        pass

    def _update_brand_status(self, connected, port=None):
        mono = pick_mono_font()
        if connected and port:
            self.device_arrow_label.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; color: {ACCENT};")
            kind, target = parse_device_selection(port)
            if kind == "ble":
                self.status_label.setText(f"BLE · {target}")
            elif kind == "usb_radio":
                self.status_label.setText(f"USB LoRa · {target or 'CH341'}")
            elif kind == "sniffer_group":
                self.status_label.setText("all LoRa sniffers")
            else:
                self.status_label.setText(f"{target} · {SERIAL_BAUD}")
        else:
            self.device_arrow_label.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; color: {DIM};")
            self.status_label.setText("not connected")

    def _update_gps_display(self):
        # No room for a GPS readout in the pocket top bar (gps_fix_label is None).
        pass

    def _settings_dialog_size(self):
        # Leaves a small margin so the dialog's own frame/title bar stays
        # inside the fixed 640x480 panel rather than exactly matching it.
        return (POCKET_WIDTH - 20, POCKET_HEIGHT - 30)
