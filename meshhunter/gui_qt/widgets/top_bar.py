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
    MESH_MESHCORE,
    MESH_MESHTASTIC,
    PANEL_2,
    SWITCH_TRACK_ON,
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
        self._compact = False
        self._port_text, self._detail_text = "", ""

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
            self._show(port_text or self._selection, detail or "")
        else:
            self._show_selection()

    def _show_selection(self):
        if self._mode == "hat":
            self._show("LoRa HAT", "SX1262 · RTL-SDR")
        else:
            self._show(self._selection or "no device", "not connected")

    def _show(self, port, detail):
        self._port_text, self._detail_text = port, detail
        if self._compact:
            # "/dev/ttyACM1" -> "ttyACM1"; "Companion · 115200" -> "Companion"
            port = port.rsplit("/", 1)[-1] if port.startswith("/") else port
            detail = "" if detail == "not connected" else detail.split(" · ")[0]
        self.port_label.setText(port)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        self.setToolTip(f"{self._port_text} · {self._detail_text}" if self._compact else "")
        self.updateGeometry()

    def set_compact(self, compact):
        self._compact = compact
        self._show(self._port_text, self._detail_text)

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
        self.setToolTip(f"GPS · {text}" if text else "GPS")

    def set_compact(self, compact):
        self.text.setVisible(not compact)


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
        # Small window: one "Advertise ▾" menu button instead.
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("sentButton")
        self.menu_button.setText("Advertise ▾")
        self.menu_button.setFixedHeight(30)
        self.menu_button.setCursor(Qt.PointingHandCursor)
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        self.menu_button.setStyleSheet(f"font-family: '{pick_sans_font()}'; font-size: 12px; font-weight: 500;"
                                       f" padding: 0 10px; color: {TXT};")
        menu = QMenu(self.menu_button)
        menu.addAction("Advertise 0-hop", self.zero_hop_clicked.emit)
        menu.addAction("Advertise flood", self.flood_clicked.emit)
        self.menu_button.setMenu(menu)
        self.menu_button.setVisible(False)
        row.addWidget(self.menu_button)
        self.set_enabled(False)

    def set_enabled(self, enabled):
        self.zero_hop_button.setEnabled(enabled)
        self.flood_button.setEnabled(enabled)
        self.menu_button.setEnabled(enabled)

    def set_compact(self, compact):
        self.caption.setVisible(not compact)
        self.segmented.setVisible(not compact)
        self.menu_button.setVisible(compact)


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
        self._sent_label = _label("SENT", DIM, 10)
        row.addWidget(self._sent_label)
        self._sync_label = _label("Sync", TXT, 12, mono=False, weight=500)
        self._sync_label.setVisible(False)
        row.addWidget(self._sync_label)
        self.wdgwars_count = _label("0", ACCENT, 12)
        row.addWidget(self.wdgwars_count)
        self._wdg_name = _label("WDGWars", DIM, 11)
        row.addWidget(self._wdg_name)
        self._ingest_sep = _label("|", LINE, 11)
        self._ingest_name = _label("Ingest", DIM, 11)
        self._ingest_bits = [self._ingest_sep, _label("0", ACCENT, 12), self._ingest_name]
        self.ingest_count = self._ingest_bits[1]
        for w in self._ingest_bits:
            row.addWidget(w)
        row.addWidget(_label("▾", DIM, 11))
        self._ingest_shown = True
        self._compact = False

        menu = QMenu(self)
        self.send_wdgwars_action = menu.addAction("Send pending to WDGWars", self.send_wdgwars_clicked.emit)
        self.send_ingest_action = menu.addAction("Send pending to Ingest API", self.send_ingest_clicked.emit)
        self._pending_sep = menu.addSeparator()
        menu.addAction("Import contacts from JSON…", self.import_json_clicked.emit)
        self.clear_action = _DangerItem("Clear device contacts", menu)
        self.clear_action.triggered.connect(self.clear_contacts_clicked.emit)
        menu.addAction(self.clear_action)
        menu.aboutToShow.connect(self._show_pending)
        self.setMenu(menu)
        # Callable -> {"wdgwars": n, "ingest": n}; set by the window.
        self.pending_counts = None
        self.set_clear_contacts_enabled(False)

    def sizeHint(self):
        return QSize(self.layout().sizeHint().width(), 30)

    def set_wdgwars_total(self, total):
        self.wdgwars_count.setText(str(total))

    def set_ingest_total(self, total):
        self.ingest_count.setText(str(total))

    def set_ingest_shown(self, shown):
        self._ingest_shown = shown
        self._apply()

    def set_compact(self, compact):
        self._compact = compact
        self._apply()

    def _apply(self):
        c = self._compact
        self._sent_label.setVisible(not c)
        self._sync_label.setVisible(c)
        self._wdg_name.setVisible(not c)
        for w in self._ingest_bits:
            w.setVisible(self._ingest_shown)
        self._ingest_name.setVisible(self._ingest_shown and not c)
        self._ingest_sep.setText("·" if c else "|")
        self.setToolTip("Sent this session: WDGWars · Ingest" if c else "")
        self.updateGeometry()

    def set_clear_contacts_enabled(self, enabled):
        self.clear_action.set_enabled(enabled)

    def set_send_wdgwars_visible(self, visible):
        self.send_wdgwars_action.setVisible(visible)
        self._update_sep()

    def set_send_ingest_visible(self, visible):
        self.send_ingest_action.setVisible(visible)
        self._update_sep()

    def _show_pending(self):
        counts = {}
        if self.pending_counts is not None:
            try:
                counts = self.pending_counts()
            except Exception:  # unreadable CSV etc.: just leave the counts off
                counts = {}
        for action, key, text in ((self.send_wdgwars_action, "wdgwars", "Send pending to WDGWars"),
                                  (self.send_ingest_action, "ingest", "Send pending to Ingest API")):
            n = counts.get(key)
            action.setText(f"{text} ({n})" if n is not None else text)

    def _update_sep(self):
        self._pending_sep.setVisible(self.send_wdgwars_action.isVisible() or self.send_ingest_action.isVisible())


def short_profile(name):
    """"Meshtastic LongFast" -> "MT LongFast"."""
    return name.replace("Meshtastic ", "MT ", 1)


def family_color(family):
    return {"meshcore": MESH_MESHCORE, "meshtastic": MESH_MESHTASTIC}.get(family, TXT)


class RadioBadge(QLabel):
    """18 px radio number badge (multi-radio, redesign 1d)."""

    def __init__(self, number, parent=None):
        super().__init__(str(number), parent)
        self.setFixedSize(18, 18)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"font-family: '{pick_mono_font()}'; font-size: 10px; font-weight: 700;"
                           f" color: {ACCENT_LIGHT}; background: {SWITCH_TRACK_ON}; border-radius: 4px;")


class RadioChip(QFrame):
    """[n] ttyACM1  MeshCore US  fixed  214 rx"""

    def __init__(self, number, parent=None):
        super().__init__(parent)
        self.setObjectName("radioChip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setStyleSheet(f"#radioChip {{ background: {PANEL_2}; border: 1px solid {LINE}; border-radius: 8px; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(7, 0, 10, 0)
        row.setSpacing(7)
        row.addWidget(RadioBadge(number))
        self.port = _label("", TXT, 12)
        self.profile = _label("", TXT, 11)
        self.state = _label("", DIM, 11)
        self.rx = _label("", ACCENT, 11)
        for w in (self.port, self.profile, self.state, self.rx):
            row.addWidget(w)

    def set_radio(self, radio, families, rx=None):
        self.port.setText(radio["label"])
        names = radio.get("profiles") or []
        if names:
            self.profile.setText(short_profile(names[0]) + (f" +{len(names) - 1}" if len(names) > 1 else ""))
            color = family_color(families.get(names[0]))
        else:
            self.profile.setText("idle")
            color = DIM
        self.profile.setStyleSheet(f"font-family: '{pick_mono_font()}'; font-size: 11px; color: {color};"
                                   " background: transparent;")
        listening = radio.get("state") == "listening"
        self.state.setText(("fixed" if len(names) == 1 else "hopping") if listening and names
                           else radio.get("state", ""))
        self.set_rx(rx)
        self._fit()
        tip = [f"{radio.get('kind', '')} on {radio.get('port', '')}", f"state: {radio.get('state', '')}"]
        if radio.get("detail"):
            tip.append(radio["detail"])
        if names:
            tip.append("profiles: " + ", ".join(names))
        self.setToolTip("\n".join(tip))

    def set_rx(self, rx):
        self.rx.setText(f"{rx} rx" if rx is not None else "")
        self.rx.setVisible(rx is not None)
        self._fit()

    def set_compact(self, compact):
        self.profile.setVisible(not compact)
        self.state.setVisible(not compact)
        self._fit()

    def _fit(self):
        # Exact width so a crowded bar can't squeeze chips into each other.
        # Polish first: the labels' font sizes come from their stylesheets.
        for lbl in (self.port, self.profile, self.state, self.rx):
            lbl.ensurePolished()
            lbl.updateGeometry()
        self.ensurePolished()  # the chip's own border comes from its stylesheet too
        self.layout().invalidate()
        self.layout().activate()
        self.setFixedWidth(self.sizeHint().width())


class RadioChips(QWidget):
    """One RadioChip per sniffer radio; only shown with two or more."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(6)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.chips = {}
        self._compact = False
        self.setVisible(False)

    def set_radios(self, radios, families):
        rx = {label: chip.rx.text() for label, chip in self.chips.items()}
        for chip in self.chips.values():
            self.row.removeWidget(chip)
            chip.hide()
            chip.setParent(None)
            chip.deleteLater()
        self.chips = {}
        multi = len(radios) > 1
        for n, radio in enumerate(radios if multi else [], start=1):
            chip = RadioChip(n)
            old = rx.get(radio["label"], "")
            chip.set_radio(radio, families, int(old.split()[0]) if old else None)
            chip.set_compact(self._compact)
            self.row.addWidget(chip)
            self.chips[radio["label"]] = chip
        self.setVisible(multi)
        self.multi_changed(multi)

    def multi_changed(self, multi):
        """Hook set by TopBar."""

    def set_rx(self, label, count):
        chip = self.chips.get(label)
        if chip is not None:
            chip.set_rx(count)

    def set_compact(self, compact):
        self._compact = compact
        for chip in self.chips.values():
            chip.set_compact(compact)


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
        self.logo = QLabel()
        self._logo_pix = QPixmap(str(LOGO_PATH))
        self._set_logo(34)
        brand.addWidget(self.logo)
        self.wordmark = _label("MESHHUNTER", ACCENT, 13, weight=700, spacing="0.1em")
        brand.addWidget(self.wordmark)
        row.addLayout(brand)
        row.addWidget(vdivider())

        self.device_chip = DeviceChip()
        row.addWidget(self.device_chip)
        self.radios = RadioChips()
        self.radios.multi_changed = self._on_multi
        row.addWidget(self.radios)
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

    COMPACT_HEIGHT = 44

    def _set_logo(self, px):
        if not self._logo_pix.isNull():
            self.logo.setPixmap(self._logo_pix.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.logo.setFixedSize(px, px)

    def set_compact(self, compact):
        """Small window (redesign 1c): 44 px bar, no wordmark, short device
        chip and GPS, Advertise and Sync as menu buttons."""
        self._compact = compact
        multi = getattr(self, "_multi", False)
        self.setFixedHeight(self.COMPACT_HEIGHT if compact else self.HEIGHT)
        self._set_logo(30 if compact else 34)
        self.wordmark.setVisible(not compact and not multi)
        self.device_chip.set_compact(compact)
        self.gps.set_compact(compact)
        self.advertise.set_compact(compact)
        self.sent.set_compact(compact or multi)
        self.radios.set_compact(compact)

    def _on_multi(self, multi):
        # Several sniffers (redesign 1d): the radio chips take the room.
        # Sniffers can't advertise, the device menu has Disconnect, and the
        # chip's dot already shows the link, so those go along with the
        # wordmark; Sync shows its short form.
        self._multi = multi
        hat = getattr(self, "_hat", False)
        compact = getattr(self, "_compact", False)
        self.advertise.setVisible(not multi and not hat)
        self.connect_button.setVisible(not multi and not hat)
        self.wordmark.setVisible(not multi and not compact)
        self.sent.set_compact(multi or compact)

    def set_hat_mode(self, hat):
        """LoRa HAT mode has no device to connect or advertise from."""
        self._hat = hat
        self.connect_button.setVisible(not hat and not getattr(self, "_multi", False))
        self.advertise.setVisible(not hat and not getattr(self, "_multi", False))
