"""Fullscreen touch UI for small LoRa-HAT devices (e.g. a 640x480
PocketTerm35): a setup screen with one big button per mesh config (plus a
Custom one leading to a radio-settings screen) and logging checkboxes, and
a live screen where the decoded packets and the RTL-SDR waterfall fill the
whole display, with a small Options button back to setup.

Shares all the radio/decoding/logging machinery with the desktop window
(LoRaWorker, WaterfallWorker, MeshCoreWorker as an advert recorder,
GPSWorker) -- only the presentation differs.
"""

import asyncio
import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QScroller,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core.config import load_config, save_config
from ..core.gps import GPSState, GPSWorker
from ..core.logging_bridge import attach_library_logging
from ..core.lora_decode import (
    LORAWAN_SYNC_WORD,
    MESHCORE_BANDWIDTHS,
    MESHCORE_SYNC_WORD,
    MESHTASTIC_PRESETS,
    MESHTASTIC_SYNC_WORD,
    meshtastic_frequency_mhz,
)
from ..core.lora_worker import LoRaWorker, radio_settings_from_config
from ..core.paths import SESSION_LOG_DIR
from ..core.rtlsdr import WaterfallWorker
from ..core.worker import MeshCoreWorker
from .bridge import Bridge
from .theme import ACCENT, LOGO_PATH, pick_mono_font
from .widgets.touch_controls import Segmented, Stepper
from .widgets.waterfall import WaterfallCanvas

MAX_PACKETS = 500

SETUP_PAGE, LIVE_PAGE, CUSTOM_PAGE = range(3)
DECODE_OPTIONS = (("MeshCore", "MeshCore"), ("Meshtastic", "Meshtastic"), ("Capture", "Capture"))
SYNC_OPTIONS = (("0x12", MESHCORE_SYNC_WORD), ("0x2B", MESHTASTIC_SYNC_WORD), ("0x34", LORAWAN_SYNC_WORD))
DEFAULT_SYNC = {"MeshCore": MESHCORE_SYNC_WORD, "Meshtastic": MESHTASTIC_SYNC_WORD}
# The HAT's SX1262 front end is matched for 860-960 MHz.
FREQ_RANGE = (860.0, 960.0)

# High-contrast colours for a small screen read in daylight -- the desktop
# theme's dim greys/greens wash out on the 3.5" panel.
BRIGHT = "#ffffff"
SOFT = "#d8e2d8"  # secondary text, still well above the desktop DIM
WARN = "#ffd23f"

# Packet list typography: the headline ("CHANNEL | NODE: message") is the
# thing to read at a glance, so it's huge, bold and pure white; the signal
# details underneath stay small and quiet.
HEADLINE_PX = 40
OTHER_HEADLINE_PX = 30  # adverts/telemetry etc -- still big, but chat wins
META_PX = 18
CHANNEL_COLOR = "#ffff00"
NODE_COLOR = "#00ffff"
META_COLOR = "#9fb09f"
_HTML_ROLE = Qt.UserRole + 1


class PacketDelegate(QStyledItemDelegate):
    """Draws each list item's HTML (see PocketWindow._add_item) with word
    wrap at the list's current width."""

    def _document(self, index, width):
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setHtml(index.data(_HTML_ROLE) or "")
        doc.setTextWidth(max(50, width))
        return doc

    def paint(self, painter, option, index):
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, Qt.darkGray)
        painter.setPen(Qt.gray)
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        doc = self._document(index, option.rect.width() - 20)
        painter.translate(option.rect.left() + 10, option.rect.top() + 6)
        doc.drawContents(painter)
        painter.restore()

    def sizeHint(self, option, index):
        width = option.widget.viewport().width() if option.widget else option.rect.width()
        doc = self._document(index, width - 20)
        return QSize(width, int(doc.size().height()) + 12)

_CHECK_ICON = (Path(__file__).parent / "assets" / "check.svg").as_posix()

# Layered on top of the app-wide STYLESHEET: everything finger-sized and
# readable at arm's length on a 3.5" 640x480 panel.
POCKET_STYLESHEET = f"""
QWidget {{ font-size: 20px; color: {BRIGHT}; }}
QPushButton#configButton {{
    background-color: #0d2226;
    border: 2px solid {ACCENT};
    border-radius: 12px;
    color: {BRIGHT};
    font-size: 22px;
    font-weight: 700;
    padding: 2px 16px;
    text-align: left;
}}
QPushButton#configButton:pressed {{ background-color: #1a4a22; }}
QPushButton#smallButton {{
    background-color: transparent;
    border: 2px solid {SOFT};
    border-radius: 10px;
    color: {BRIGHT};
    font-size: 20px;
    font-weight: 600;
    padding: 4px 14px;
}}
QPushButton#smallButton:pressed {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#touchStep, QPushButton#touchSegment {{
    background-color: #16241a;
    border: 2px solid {SOFT};
    border-radius: 10px;
    color: {BRIGHT};
    font-size: 21px;
    font-weight: 700;
    padding: 0 6px;
}}
QPushButton#touchStep:pressed {{ background-color: #1a4a22; border-color: {ACCENT}; }}
QPushButton#touchSegment:checked {{ background-color: {ACCENT}; border-color: {ACCENT}; color: #000000; }}
QLineEdit#touchValue {{
    background-color: #111511;
    border: 2px solid {SOFT};
    border-radius: 10px;
    color: {BRIGHT};
    font-size: 23px;
    font-weight: 700;
    padding: 0 6px;
}}
QLineEdit#touchValue:focus {{ border-color: {ACCENT}; }}
QLabel#fieldLabel {{ font-size: 19px; font-weight: 700; color: {BRIGHT}; }}
QCheckBox {{ font-size: 20px; spacing: 10px; color: {BRIGHT}; }}
QCheckBox::indicator {{
    width: 32px; height: 32px;
    border: 2px solid {SOFT};
    border-radius: 7px;
    background: #111511;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: url({_CHECK_ICON});
}}
QListWidget#packetList {{
    background-color: #050705;
    border: none;
    font-size: 20px;
}}
QListWidget#packetList::item {{
    border-bottom: 1px solid #3a4a3a;
    padding: 5px 8px;
}}
QListWidget#packetList::item:selected {{ background: #0e1f11; }}
QScrollBar:vertical {{ width: 16px; }}
QScrollBar::handle:vertical {{ min-height: 40px; border-radius: 8px; }}
QSplitter::handle:vertical {{ background: {SOFT}; height: 8px; }}
"""

LOG_OPTIONS = (
    ("log_packets", "Save packets to CSV"),
    ("log_nodes", "Log MeshCore nodes"),
    ("log_gps", "Tag with GPS location"),
    ("log_session", "Session log file"),
)


def _config_choices(config):
    """(label, subtitle, config overrides) for the three setup buttons."""
    mc = (
        "MeshCore",
        f"{float(config['meshcore_freq']):.3f} MHz · SF{config['meshcore_sf']} · BW{float(config['meshcore_bw']):g}",
        {"lora_network": "MeshCore"},
    )
    region = config.get("meshtastic_region", "US")
    choices = [mc]
    for preset in ("LongFast", "MediumFast"):
        bw, sf, _ = MESHTASTIC_PRESETS[preset]
        freq = meshtastic_frequency_mhz(region, preset)
        choices.append((
            f"Meshtastic {preset}",
            f"{freq:.3f} MHz · SF{sf} · BW{bw:g} · {region}",
            {"lora_network": "Meshtastic", "meshtastic_preset": preset},
        ))
    return choices


def _custom_subtitle(config):
    decode = "Packet Capture" if config["custom_decode"] == "Capture" else config["custom_decode"]
    return (
        f"{decode} · {float(config['custom_freq']):.3f} MHz · SF{config['custom_sf']}"
        f" · BW{float(config['custom_bw']):g}"
    )


class PocketWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeshHunter")
        self.setWindowIcon(QIcon(str(LOGO_PATH)))
        self.setStyleSheet(POCKET_STYLESHEET)
        self._mono = pick_mono_font()

        self.config_data = load_config()
        self.bridge = Bridge()
        attach_library_logging(self.bridge)
        self.gps_state = GPSState()
        self.gps_worker = None
        self.lora_worker = None
        self.waterfall_worker = None
        self.recorder = None
        self.session_log_file = None
        self.packet_count = 0

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_setup_page())
        self.stack.addWidget(self._build_live_page())
        self.stack.addWidget(self._build_custom_page())
        self.setCentralWidget(self.stack)
        self._wire_signals()

        self._start_gps_worker()
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_gps_labels)
        self._status_timer.start(1000)
        self._update_gps_labels()

    # ---- setup page -----------------------------------------------------------

    def _build_setup_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(8)

        top = QHBoxLayout()
        logo = QPixmap(str(LOGO_PATH))
        if not logo.isNull():
            logo_label = QLabel()
            logo_label.setPixmap(logo.scaledToHeight(40, Qt.SmoothTransformation))
            top.addWidget(logo_label)
        title = QLabel("MeshHunter")
        title.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {ACCENT};")
        top.addWidget(title)
        top.addStretch(1)
        self.setup_gps_label = QLabel()
        self.setup_gps_label.setStyleSheet(f"font-family: '{self._mono}'; font-size: 15px;")
        top.addWidget(self.setup_gps_label)
        exit_btn = self._small_button("Exit")
        exit_btn.clicked.connect(self.close)
        top.addWidget(exit_btn)
        layout.addLayout(top)

        self.config_buttons = []
        for label, subtitle, overrides in _config_choices(self.config_data):
            btn = QPushButton(f"{label}\n{subtitle}")
            btn.setObjectName("configButton")
            btn.setMinimumHeight(64)
            btn.clicked.connect(lambda _=False, o=overrides: self._start_listening(o))
            layout.addWidget(btn)
            self.config_buttons.append(btn)
        self.custom_button = QPushButton()
        self.custom_button.setObjectName("configButton")
        self.custom_button.setMinimumHeight(64)
        self.custom_button.clicked.connect(self._open_custom_page)
        layout.addWidget(self.custom_button)
        self.config_buttons.append(self.custom_button)
        self._refresh_custom_button()

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        self.log_checks = {}
        for i, (key, text) in enumerate(LOG_OPTIONS):
            cb = QCheckBox(text)
            cb.setMinimumHeight(40)
            cb.setChecked(bool(self.config_data.get(key, True)))
            cb.toggled.connect(lambda checked, k=key: self._set_log_option(k, checked))
            grid.addWidget(cb, i // 2, i % 2)
            self.log_checks[key] = cb
        layout.addLayout(grid)
        layout.addStretch(1)
        return page

    def _refresh_custom_button(self):
        self.custom_button.setText(f"Custom\n{_custom_subtitle(self.config_data)}")

    # ---- custom radio page ------------------------------------------------------

    def _build_custom_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        def label(text):
            lbl = QLabel(text)
            lbl.setObjectName("fieldLabel")
            return lbl

        self.decode_select = Segmented(DECODE_OPTIONS)
        self.decode_select.changed.connect(self._on_decode_changed)
        self.freq_stepper = Stepper(*FREQ_RANGE, step=0.025, decimals=3, big_step=1)
        self.bw_select = Segmented([(f"{bw:g}", bw) for bw in MESHCORE_BANDWIDTHS])
        self.sf_stepper = Stepper(5, 12, step=1)
        self.cr_stepper = Stepper(5, 8, step=1, fmt=lambda v: f"4/{v}")

        sync_row = QWidget()
        sync_layout = QHBoxLayout(sync_row)
        sync_layout.setContentsMargins(0, 0, 0, 0)
        sync_layout.setSpacing(6)
        self.sync_select = Segmented(SYNC_OPTIONS)
        self.sync_select.changed.connect(lambda v: self.sync_field.setText(f"0x{v:02X}"))
        sync_layout.addWidget(self.sync_select, stretch=3)
        self.sync_field = QLineEdit()
        self.sync_field.setObjectName("touchValue")
        self.sync_field.setAlignment(Qt.AlignCenter)
        self.sync_field.setMinimumHeight(50)
        self.sync_field.setFixedWidth(96)
        self.sync_field.editingFinished.connect(self._on_sync_edited)
        sync_layout.addWidget(self.sync_field)

        sf_cr_row = QWidget()
        sf_cr = QHBoxLayout(sf_cr_row)
        sf_cr.setContentsMargins(0, 0, 0, 0)
        sf_cr.setSpacing(8)
        sf_cr.addWidget(self.sf_stepper, stretch=1)
        sf_cr.addWidget(label("CR"))
        sf_cr.addWidget(self.cr_stepper, stretch=1)

        self.channel_label = label("Chan")
        self.channel_field = QLineEdit()
        self.channel_field.setObjectName("touchValue")
        self.channel_field.setMinimumHeight(50)
        self.channel_field.setPlaceholderText("Meshtastic channel name")

        rows = (
            ("Decode", self.decode_select),
            ("MHz", self.freq_stepper),
            ("BW kHz", self.bw_select),
            ("SF", sf_cr_row),
            ("Sync", sync_row),
        )
        for i, (text, widget) in enumerate(rows):
            grid.addWidget(label(text), i, 0)
            grid.addWidget(widget, i, 1)
        grid.addWidget(self.channel_label, len(rows), 0)
        grid.addWidget(self.channel_field, len(rows), 1)
        layout.addLayout(grid)
        layout.addStretch(1)

        bottom = QHBoxLayout()
        back_btn = self._small_button("Back")
        back_btn.setMinimumHeight(54)
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(SETUP_PAGE))
        bottom.addWidget(back_btn)
        bottom.addStretch(1)
        start_btn = QPushButton("Start listening")
        start_btn.setObjectName("configButton")
        start_btn.setMinimumHeight(54)
        start_btn.clicked.connect(self._start_custom)
        bottom.addWidget(start_btn, stretch=2)
        layout.addLayout(bottom)
        return page

    def _open_custom_page(self):
        c = self.config_data
        self.decode_select.setValue(c["custom_decode"])
        self.freq_stepper.setValue(float(c["custom_freq"]))
        self.bw_select.setValue(float(c["custom_bw"]))
        self.sf_stepper.setValue(int(c["custom_sf"]))
        self.cr_stepper.setValue(int(c["custom_cr"]))
        self._set_sync(int(c["custom_sync"]))
        self.channel_field.setText(c.get("custom_channel") or "LongFast")
        self._update_channel_visibility()
        self.stack.setCurrentIndex(CUSTOM_PAGE)

    def _on_decode_changed(self, decode):
        # Switching decoder implies that network's sync word; Capture keeps
        # whatever is set, since raw capture is often about trying others.
        if decode in DEFAULT_SYNC:
            self._set_sync(DEFAULT_SYNC[decode])
        self._update_channel_visibility()

    def _update_channel_visibility(self):
        meshtastic = self.decode_select.value() == "Meshtastic"
        self.channel_label.setVisible(meshtastic)
        self.channel_field.setVisible(meshtastic)

    def _set_sync(self, value):
        self.sync_select.setValue(value)
        self.sync_field.setText(f"0x{value:02X}")

    def _sync_value(self):
        try:
            value = int(self.sync_field.text().strip(), 16)
        except ValueError:
            return None
        return value if 0 <= value <= 0xFF else None

    def _on_sync_edited(self):
        value = self._sync_value()
        self._set_sync(value if value is not None else int(self.config_data["custom_sync"]))

    def _start_custom(self):
        sync = self._sync_value()
        self.config_data.update({
            "custom_decode": self.decode_select.value() or "Capture",
            "custom_freq": self.freq_stepper.value(),
            "custom_bw": self.bw_select.value() or 125.0,
            "custom_sf": int(self.sf_stepper.value()),
            "custom_cr": int(self.cr_stepper.value()),
            "custom_sync": sync if sync is not None else int(self.config_data["custom_sync"]),
            "custom_channel": self.channel_field.text().strip() or "LongFast",
        })
        self._refresh_custom_button()
        self._start_listening({"lora_network": "Custom"})

    def _small_button(self, text):
        btn = QPushButton(text)
        btn.setObjectName("smallButton")
        btn.setMinimumHeight(40)
        btn.setFocusPolicy(Qt.NoFocus)
        return btn

    def _set_log_option(self, key, checked):
        self.config_data[key] = checked
        save_config(self.config_data)

    # ---- live page ------------------------------------------------------------

    def _build_live_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("terminalHeader")
        bar.setFixedHeight(48)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 4, 6, 4)
        row.setSpacing(10)
        self.live_title = QLabel()
        self.live_title.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {ACCENT};")
        row.addWidget(self.live_title)
        self.live_info = QLabel()
        self.live_info.setStyleSheet(f"font-size: 17px; color: {BRIGHT};")
        # Ignored: long info text gets clipped instead of widening the
        # window past the 640 px screen.
        self.live_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.live_info, stretch=1)
        options_btn = self._small_button("Options")
        options_btn.clicked.connect(self._back_to_setup)
        row.addWidget(options_btn)
        layout.addWidget(bar)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        self.packet_list = QListWidget()
        self.packet_list.setObjectName("packetList")
        self.packet_list.setItemDelegate(PacketDelegate(self.packet_list))
        # Re-wrap every item when the width changes.
        self.packet_list.setResizeMode(QListWidget.Adjust)
        self.packet_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.packet_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.packet_list.setFocusPolicy(Qt.NoFocus)
        # Finger-drag (kinetic) scrolling instead of needing the scrollbar.
        QScroller.grabGesture(self.packet_list.viewport(), QScroller.LeftMouseButtonGesture)
        splitter.addWidget(self.packet_list)
        self.waterfall = WaterfallCanvas(font_px=17, spectrum_height=44)
        self.waterfall.setMinimumHeight(120)
        splitter.addWidget(self.waterfall)
        splitter.setSizes([240, 190])
        layout.addWidget(splitter, stretch=1)
        return page

    # ---- wiring ---------------------------------------------------------------

    def _wire_signals(self):
        self.bridge.hat_packet.connect(self._on_packet)
        self.bridge.hat_status.connect(self._on_hat_status)
        self.bridge.hat_error.connect(lambda msg: self._add_notice(f"LoRa radio error: {msg}", WARN))
        self.bridge.waterfall_row.connect(self.waterfall.add_row)
        self.bridge.waterfall_error.connect(lambda msg: self._add_notice(f"RTL-SDR error: {msg}", WARN))
        self.bridge.log.connect(self._session_log)
        self.bridge.status.connect(self._session_log)
        self.bridge.error.connect(self._session_log)

    # ---- start / stop ---------------------------------------------------------

    def _start_listening(self, overrides):
        self.config_data.update(overrides)
        save_config(self.config_data)
        settings = radio_settings_from_config(self.config_data)
        gps_state = self.gps_state if self.config_data.get("log_gps", True) else None

        if self.config_data.get("log_session"):
            self._open_session_log(settings.network)
        self._session_log(f"-- Listening: {settings.label} --")

        if self.config_data.get("log_nodes", True) and settings.network == "MeshCore":
            self.recorder = MeshCoreWorker(None, self.bridge, gps_state=gps_state)
            self.recorder.apply_config(self.config_data)
            self.recorder.load_known_nodes()
        else:
            self.recorder = None

        self.packet_list.clear()
        self.packet_count = 0
        if settings.custom:
            self.live_title.setText(f"Custom · {settings.network}")  # short: the bar is tight at 640 px
        elif settings.network == "MeshCore":
            self.live_title.setText(settings.network)
        else:
            self.live_title.setText(settings.channel_name)
        # Meshtastic text has no channel name in the packet; label it with
        # the channel we're decoding (see _packet_html).
        self._meshtastic_channel = settings.channel_name
        self._live_freq = f"{settings.freq_mhz:.3f} MHz"
        self._update_gps_labels()
        self.stack.setCurrentIndex(LIVE_PAGE)

        self.lora_worker = LoRaWorker(
            settings, self.bridge, gps_state, log_csv=self.config_data.get("log_packets", True)
        )
        self.lora_worker.start()

        center_hz = int(settings.freq_mhz * 1e6)
        sample_rate = int(self.config_data.get("sdr_sample_rate") or 2_400_000)
        self.waterfall.configure(center_hz, sample_rate, settings.bw_khz * 1e3)
        self.waterfall_worker = WaterfallWorker(
            center_hz,
            sample_rate,
            on_row=self.bridge.waterfall_row.emit,
            on_error=self.bridge.waterfall_error.emit,
            gain_db=self.config_data.get("sdr_gain"),
        )
        self.waterfall_worker.start()

    def _stop_listening(self):
        # Joined so the next config's workers can claim the SX1262 GPIO
        # lines / RTL-SDR (see MainWindow._stop_hat).
        for worker in (self.lora_worker, self.waterfall_worker):
            if worker is not None:
                worker.stop()
                worker.join(timeout=2)
        self.lora_worker = None
        self.waterfall_worker = None
        self.recorder = None
        self._close_session_log()

    def _back_to_setup(self):
        self._stop_listening()
        self.stack.setCurrentIndex(SETUP_PAGE)

    # ---- packets ----------------------------------------------------------------

    def _on_packet(self, ev):
        p = ev.packet
        self.packet_count += 1
        self._update_gps_labels()
        meta = f"{ev.rssi:.0f} dBm  SNR {ev.snr:+.1f}  {p.hops}"
        self._add_item(self._packet_html(ev, meta))
        self._session_log(f"[{ev.time:%H:%M:%S}] RX {p.network} {p.kind}: {meta} from={p.sender!r} {p.text}")

        if self.recorder is not None and ev.crc_ok and p.kind == "ADVERT" and p.node:
            asyncio.ensure_future(self.recorder.record_advert(p.node))

    def _on_hat_status(self, payload):
        self._session_log(f"-- LoRa HAT: {payload} --")
        if payload.startswith("Listening"):
            self._add_notice("Listening…", SOFT)

    def _packet_html(self, ev, meta):
        p = ev.packet
        esc = html.escape
        is_chat = p.kind == "TEXT" or p.kind.startswith("CHAN ")
        if p.kind.startswith("CHAN "):
            label = p.kind[5:]
        elif p.kind == "TEXT":
            label = self._meshtastic_channel
        else:
            label = p.kind
        size = HEADLINE_PX if is_chat else OTHER_HEADLINE_PX
        label_color = WARN if not ev.crc_ok else CHANNEL_COLOR
        headline = f'<span style="color:{label_color}">{esc(label)}</span>'
        if p.sender:
            headline += f' <span style="color:{BRIGHT}">|</span> <span style="color:{NODE_COLOR}">{esc(p.sender)}</span>'
        if p.text:
            headline += f'<span style="color:{BRIGHT}">: {esc(p.text)}</span>'
        return (
            f'<div style="font-size:{size}px; font-weight:800; color:{BRIGHT};">{headline}</div>'
            f'<div style="font-size:{META_PX}px; color:{META_COLOR};">{ev.time:%H:%M:%S}  {esc(meta)}</div>'
        )

    def _add_notice(self, text, color):
        self._add_item(f'<div style="font-size:{OTHER_HEADLINE_PX}px; font-weight:700; color:{color};">{html.escape(text)}</div>')
        self._session_log(f"-- {text} --")

    def _add_item(self, item_html):
        scrollbar = self.packet_list.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        item = QListWidgetItem()
        item.setData(_HTML_ROLE, item_html)
        self.packet_list.addItem(item)
        while self.packet_list.count() > MAX_PACKETS:
            self.packet_list.takeItem(0)
        if at_bottom:
            self.packet_list.scrollToBottom()

    # ---- GPS ----------------------------------------------------------------------

    def _start_gps_worker(self):
        port = self.config_data.get("gps_port", "").strip()
        if self.config_data.get("gps_enabled", True) and port:
            self.gps_worker = GPSWorker(port, self.gps_state, self.bridge)
            self.gps_worker.start()

    def _gps_text(self):
        if self.gps_worker is None:
            return "GPS off", SOFT
        fix = self.gps_state.fix
        if fix:
            return f"{fix[0]:.4f}, {fix[1]:.4f}", ACCENT
        return "GPS: no fix", WARN

    def _update_gps_labels(self):
        text, color = self._gps_text()
        self.setup_gps_label.setText(text)
        self.setup_gps_label.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {color};")
        freq = getattr(self, "_live_freq", "")
        self.live_info.setText(f"{freq} · {self.packet_count} pkts · {text}")
        self.live_info.setStyleSheet(f"font-size: 17px; color: {color if color == WARN else BRIGHT};")

    # ---- session log ------------------------------------------------------------

    def _open_session_log(self, label):
        self._close_session_log()
        try:
            SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
            path = SESSION_LOG_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_lora_{label.lower()}.log"
            self.session_log_file = path.open("w", encoding="utf-8")
        except OSError as exc:
            self.session_log_file = None
            self._add_notice(f"Could not open session log: {exc}", WARN)

    def _close_session_log(self):
        if self.session_log_file is not None:
            try:
                self.session_log_file.close()
            except OSError:
                pass
            self.session_log_file = None

    def _session_log(self, line):
        if self.session_log_file is None:
            return
        try:
            self.session_log_file.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}\n")
            self.session_log_file.flush()
        except OSError:
            self._close_session_log()

    # ---- lifecycle --------------------------------------------------------------

    def closeEvent(self, event):
        self._stop_listening()
        if self.gps_worker is not None:
            self.gps_worker.stop()
        super().closeEvent(event)
