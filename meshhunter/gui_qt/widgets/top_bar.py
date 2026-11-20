"""Desktop top bar (redesign 1a): replaces the old 380 px left rail.

Left to right: logo + wordmark | device chip, LIVE pill, Connect/Disconnect
| GPS indicator | stretch | ADVERTISE 0-hop|Flood | SENT menu | Settings.

The pieces keep the same method/signal names as the rail panels they
replace, so MainWindow's logic doesn't care which layout it's driving:

  DeviceChip      ~ ConnectionPanel (set_devices, device_selection, ...)
  AdvertiseGroup  ~ AdvertisePanel  (zero_hop_clicked, flood_clicked, set_enabled)
  SentButton      ~ SyncPanel       (send_*_clicked, set_*_total, ...)
"""

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from ..theme import (
    ACCENT,
    ACCENT_DIM,
    ACCENT_LIGHT,
    DANGER_HOVER_TEXT,
    DANGER_TEXT,
    DIM,
    LINE,
    LIVE_PILL_BG,
    LOGO_PATH,
    LOG_WARN,
    TXT,
    pick_mono_font,
    pick_sans_font,
)
from .common import make_button


def vdivider(height=26):
    line = QFrame()
    line.setProperty("class", "VDivider")
    line.setFixedSize(1, height)
    return line


def _label(text, color, px, mono=True, weight=None, spacing=None):
    lbl = QLabel(text)
    family = pick_mono_font() if mono else pick_sans_font()
    style = f"font-family: '{family}'; font-size: {px}px; color: {color}; background: transparent;"
    if weight:
        style += f" font-weight: {weight};"
    if spacing:
        style += f" letter-spacing: {spacing};"
    lbl.setStyleSheet(style)
    lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
    return lbl


class Dot(QWidget):
    """A small status dot: filled (optionally glowing) or a hollow ring."""

    def __init__(self, size=7, parent=None):
        super().__init__(parent)
        self._size = size
        self._color = QColor(DIM)
        self._ring = False
        self._glow = False
        self.setFixedSize(size + 6, size + 6)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_state(self, color, ring=False, glow=False):
        self._color, self._ring, self._glow = QColor(color), ring, glow
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(3, 3, self._size, self._size)
        if self._glow:
            halo = QColor(self._color)
            halo.setAlpha(60)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(r.adjusted(-3, -3, 3, 3))
        if self._ring:
            p.setPen(self._color)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(0.5, 0.5, -0.5, -0.5))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(self._color)
            p.drawEllipse(r)


class DeviceChip(QToolButton):
    """Status dot, port, mode detail and a chevron; the menu holds the mode
    choice, the device list, refresh and Connect/Disconnect."""

    refresh_clicked = Signal()
    connect_clicked = Signal()
    mode_selected = Signal(str)
    hat_settings_clicked = Signal()
    connect_text_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("deviceChip")
        self.setFixedHeight(32)
        self.setCursor(Qt.PointingHandCursor)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._devices = []
        self._selection = ""
        self._connect_text = "Connect"
        self._modes = []
        self._mode = "companion"
        self._connected = False

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 10, 0)
        row.setSpacing(8)
        self.dot = Dot(7)
        row.addWidget(self.dot)
        self.port_label = _label("no device", TXT, 12)
        row.addWidget(self.port_label)
        self.detail_label = _label("", DIM, 11)
        row.addWidget(self.detail_label)
        row.addWidget(_label("▾", DIM, 11))

        self._menu = QMenu(self)
        self._menu.aboutToShow.connect(self._rebuild_menu)
        self.setMenu(self._menu)
        self.set_status(False)

    def sizeHint(self):
        return QSize(self.layout().sizeHint().width(), 32)

    # ---- ConnectionPanel-compatible API ------------------------------------

    def set_devices(self, values):
        self._devices = list(values)

    def device_selection(self):
        return self._selection.strip()

    def set_device_selection(self, value):
        self._selection = value or ""
        if not self._connected:
            self._show_selection()

    def set_connect_text(self, text):
        self._connect_text = text
        self.connect_text_changed.emit(text)

    # ---- mode / status -----------------------------------------------------

    def set_modes(self, modes):
        """[(key, label)]; the mode section is left out with only one."""
        self._modes = list(modes)

    def set_mode(self, mode):
        self._mode = mode
        if not self._connected:
            self._show_selection()

    def set_status(self, connected, port_text=None, detail=None):
        self._connected = connected
        self.dot.set_state(ACCENT if connected else DIM, glow=connected)
        if connected:
            self.port_label.setText(port_text or self._selection)
            self.detail_label.setText(detail or "")
        else:
            self._show_selection()
        self.updateGeometry()

    def _show_selection(self):
        if self._mode == "hat":
            self.port_label.setText("LoRa HAT")
            self.detail_label.setText("SX1262 · RTL-SDR")
        else:
            self.port_label.setText(self._selection or "no device")
            self.detail_label.setText("not connected")
        self.updateGeometry()

    def _rebuild_menu(self):
        menu = self._menu
        menu.clear()
        if len(self._modes) > 1:
            menu.addSection("Mode")
            group = QActionGroup(menu)
            for key, label in self._modes:
                act = QAction(label, menu, checkable=True)
                act.setChecked(key == self._mode)
                act.triggered.connect(lambda _=False, k=key: self.mode_selected.emit(k))
                group.addAction(act)
                menu.addAction(act)
        if self._mode == "hat":
            menu.addSeparator()
            menu.addAction("LoRa HAT settings…", self.hat_settings_clicked.emit)
            return
        menu.addSection("Device")
        group = QActionGroup(menu)
        for value in self._devices or []:
            act = QAction(value, menu, checkable=True)
            act.setChecked(value == self._selection)
            act.setEnabled(not self._connected)
            act.triggered.connect(lambda _=False, v=value: self.set_device_selection(v))
            group.addAction(act)
            menu.addAction(act)
        if not self._devices:
            empty = menu.addAction("No devices found")
            empty.setEnabled(False)
        other = menu.addAction("Other device…", self._ask_other)
        other.setEnabled(not self._connected)
        menu.addAction("↻  Refresh devices", self.refresh_clicked.emit)
        menu.addSeparator()
        menu.addAction(self._connect_text, self.connect_clicked.emit)

    def _ask_other(self):
        text, ok = QInputDialog.getText(self, "Device", "Serial port, BLE address or device string:",
                                        text=self._selection)
        if ok and text.strip():
            self.set_device_selection(text.strip())


class LiveBadge(QLabel):
    """The LIVE pill: bright while connected, dimmed otherwise."""

    def __init__(self, parent=None):
        super().__init__("LIVE", parent)
        self._mono = pick_mono_font()
        self.setFixedHeight(20)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAlignment(Qt.AlignCenter)
        self.set_connected(False)

    def set_connected(self, connected):
        if connected:
            color, border, bg = ACCENT, ACCENT_DIM, LIVE_PILL_BG
        else:
            color, border, bg = DIM, LINE, "transparent"
        self.setStyleSheet(
            f"font-family: '{self._mono}'; font-size: 10px; font-weight: 600; letter-spacing: 0.08em;"
            f" color: {color}; background-color: {bg}; border: 1px solid {border};"
            f" border-radius: 10px; padding: 3px 8px;"
        )


class GpsIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.dot = Dot(8)
        row.addWidget(self.dot)
        row.addWidget(_label("GPS", DIM, 11))
        self.text = _label("", LOG_WARN, 11)
        row.addWidget(self.text)

    def set_state(self, state, text=""):
        """state: "fix" (text = "lat, lon"), "nofix", "off" (configured but
        disabled/unset) or "hidden"."""
        self.setVisible(state != "hidden")
        mono = pick_mono_font()
        if state == "fix":
            self.dot.set_state(ACCENT)
            color = ACCENT_LIGHT
        elif state == "nofix":
            self.dot.set_state(LOG_WARN, ring=True)
            color, text = LOG_WARN, text or "waiting for fix…"
        else:
            self.dot.set_state(DIM, ring=True)
            color = DIM
        self.text.setText(text)
        self.text.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; color: {color}; background: transparent;")


class Segmented(QWidget):
    """Buttons sharing one rounded border with 1 px dividers between them."""

    def __init__(self, labels, height=30, checkable=False, parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(height)
        row = QHBoxLayout(self)
        row.setContentsMargins(1, 1, 1, 1)
        row.setSpacing(0)
        sans = pick_sans_font()
        self.buttons = []
        for i, text in enumerate(labels):
            if i:
                sep = QFrame()
                sep.setFixedWidth(1)
                row.addWidget(sep)
            btn = QPushButton(text)
            btn.setCheckable(checkable)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(height - 2)
            btn.setStyleSheet(f"font-family: '{sans}'; font-size: 12px; font-weight: 500;")
            btn.setMinimumWidth(btn.fontMetrics().horizontalAdvance(text) + 28)
            row.addWidget(btn)
            self.buttons.append(btn)


class AdvertiseGroup(QWidget):
    zero_hop_clicked = Signal()
    flood_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.caption = _label("ADVERTISE", DIM, 10, spacing="0.08em")
        row.addWidget(self.caption)
        self.segmented = Segmented(["0-hop", "Flood"])
        self.zero_hop_button, self.flood_button = self.segmented.buttons
        self.zero_hop_button.clicked.connect(self.zero_hop_clicked)
        self.flood_button.clicked.connect(self.flood_clicked)
        row.addWidget(self.segmented)
        self.set_enabled(False)

    def set_enabled(self, enabled):
        self.zero_hop_button.setEnabled(enabled)
        self.flood_button.setEnabled(enabled)


class _DangerItem(QWidgetAction):
    """A menu item in danger colors (QMenu QSS can't style one action)."""

    def __init__(self, text, menu):
        super().__init__(menu)
        self.button = QPushButton(text)
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.setStyleSheet(
            f"QPushButton {{ text-align: left; padding: 6px 18px 6px 12px; border: none; border-radius: 6px;"
            f" background: transparent; color: {DANGER_TEXT}; }}"
            f"QPushButton:hover {{ background-color: #2a1414; color: {DANGER_HOVER_TEXT}; }}"
            f"QPushButton:disabled {{ color: {DIM}; }}"
        )
        self.button.clicked.connect(self.trigger)
        self.button.clicked.connect(menu.close)
        self.setDefaultWidget(self.button)

    def set_enabled(self, enabled):
        self.button.setEnabled(enabled)
        self.setEnabled(enabled)


class SentButton(QToolButton):
    """SENT n WDGWars | n Ingest ▾ -- the sync menu."""

    send_wdgwars_clicked = Signal()
    send_ingest_clicked = Signal()
    clear_contacts_clicked = Signal()
    import_json_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sentButton")
        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(6)
        row.addWidget(_label("SENT", DIM, 10))
        self.wdgwars_count = _label("0", ACCENT, 12)
        row.addWidget(self.wdgwars_count)
        row.addWidget(_label("WDGWars", DIM, 11))
        self._ingest_bits = [_label("|", LINE, 11), _label("0", ACCENT, 12), _label("Ingest", DIM, 11)]
        self.ingest_count = self._ingest_bits[1]
        for w in self._ingest_bits:
            row.addWidget(w)
        row.addWidget(_label("▾", DIM, 11))

        menu = QMenu(self)
        self.send_wdgwars_action = menu.addAction("Send pending to WDGWars", self.send_wdgwars_clicked.emit)
        self.send_ingest_action = menu.addAction("Send pending to Ingest API", self.send_ingest_clicked.emit)
        self._pending_sep = menu.addSeparator()
        menu.addAction("Import contacts from JSON…", self.import_json_clicked.emit)
        self.clear_action = _DangerItem("Clear device contacts", menu)
        self.clear_action.triggered.connect(self.clear_contacts_clicked.emit)
        menu.addAction(self.clear_action)
        self.setMenu(menu)
        self.set_clear_contacts_enabled(False)

    def sizeHint(self):
        return QSize(self.layout().sizeHint().width(), 30)

    def set_wdgwars_total(self, total):
        self.wdgwars_count.setText(str(total))

    def set_ingest_total(self, total):
        self.ingest_count.setText(str(total))

    def set_ingest_shown(self, shown):
        for w in self._ingest_bits:
            w.setVisible(shown)
        self.updateGeometry()

    def set_clear_contacts_enabled(self, enabled):
        self.clear_action.set_enabled(enabled)

    def set_send_wdgwars_visible(self, visible):
        self.send_wdgwars_action.setVisible(visible)
        self._update_sep()

    def set_send_ingest_visible(self, visible):
        self.send_ingest_action.setVisible(visible)
        self._update_sep()

    def _update_sep(self):
        self._pending_sep.setVisible(self.send_wdgwars_action.isVisible() or self.send_ingest_action.isVisible())


class TopBar(QFrame):
    settings_clicked = Signal()

    HEIGHT = 52

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setFixedHeight(self.HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(12)

        brand = QHBoxLayout()
        brand.setSpacing(8)
        logo = QLabel()
        pix = QPixmap(str(LOGO_PATH))
        if not pix.isNull():
            logo.setPixmap(pix.scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo.setFixedSize(34, 34)
        brand.addWidget(logo)
        self.wordmark = _label("MESHHUNTER", ACCENT, 13, weight=700, spacing="0.1em")
        brand.addWidget(self.wordmark)
        row.addLayout(brand)
        row.addWidget(vdivider())

        self.device_chip = DeviceChip()
        row.addWidget(self.device_chip)
        self.live = LiveBadge()
        row.addWidget(self.live)
        self.connect_button = make_button("Connect", variant="secondary", height=30)
        self.connect_button.setFixedHeight(30)
        self.connect_button.clicked.connect(self.device_chip.connect_clicked)
        self.device_chip.connect_text_changed.connect(self.connect_button.setText)
        row.addWidget(self.connect_button)

        self.gps_divider = vdivider()
        row.addWidget(self.gps_divider)
        self.gps = GpsIndicator()
        row.addWidget(self.gps)

        row.addStretch(1)

        self.advertise = AdvertiseGroup()
        row.addWidget(self.advertise)
        self.sent = SentButton()
        row.addWidget(self.sent)
        settings = make_button("Settings", variant="primary", height=30)
        settings.setFixedHeight(30)
        settings.setToolTip("Settings (Ctrl+,)")
        settings.clicked.connect(self.settings_clicked)
        row.addWidget(settings)

    def set_gps(self, state, text=""):
        self.gps.set_state(state, text)
        self.gps_divider.setVisible(state != "hidden")

    NARROW = 1280

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.set_narrow(self.width() <= self.NARROW)

    def set_narrow(self, narrow):
        """Below ~1280 px: drop the wordmark, the chip's mode detail, the GPS
        text and the ADVERTISE caption so the controls keep their size."""
        self.wordmark.setVisible(not narrow)
        self.device_chip.detail_label.setVisible(not narrow)
        self.device_chip.updateGeometry()
        self.gps.text.setVisible(not narrow)
        self.advertise.caption.setVisible(not narrow)

    def set_hat_mode(self, hat):
        """LoRa HAT mode has no device to connect or advertise from."""
        self.connect_button.setVisible(not hat)
        self.advertise.setVisible(not hat)
