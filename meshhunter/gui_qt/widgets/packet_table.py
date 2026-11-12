"""DECODED PACKETS pane: one row per packet the LoRa HAT heard, newest at
the bottom (auto-scrolling unless the user has scrolled up to read)."""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..theme import ACCENT, DIM, LOG_OK, LOG_WARN, TERMINAL_DIVIDER, TXT, pick_mono_font
from .common import make_button, section_label

MAX_ROWS = 2000
COLUMNS = ("Time", "RSSI / SNR", "Type", "From", "Message", "Route")


class PacketTable(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("terminalPanel")
        self._mono = pick_mono_font()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setObjectName("packetTable")
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setStyleSheet(f"font-family: '{self._mono}'; font-size: 12px;")
        self.table.verticalHeader().setDefaultSectionSize(22)
        header = self.table.horizontalHeader()
        for col in range(len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.table, stretch=1)

    def _build_header(self):
        header = QFrame()
        header.setObjectName("terminalHeader")
        header.setFixedHeight(44)
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 0, 16, 0)

        title = section_label("DECODED PACKETS")
        title.setStyleSheet(title.styleSheet() + f" color: {ACCENT};")
        row.addWidget(title)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"background-color: {TERMINAL_DIVIDER}; max-height: 1px; border: none;")
        row.addWidget(line, stretch=1)

        self.status_label = QLabel("stopped")
        self.status_label.setStyleSheet(f"font-family: '{self._mono}'; font-size: 11px; color: {DIM};")
        row.addWidget(self.status_label)

        clear_btn = make_button("Clear", variant="secondary", height=26)
        clear_btn.clicked.connect(self.clear)
        row.addWidget(clear_btn)
        return header

    def set_status(self, text, active=False):
        self.status_label.setText(text)
        color = ACCENT if active else DIM
        self.status_label.setStyleSheet(f"font-family: '{self._mono}'; font-size: 11px; color: {color};")

    def add_event(self, ev):
        p = ev.packet
        scrollbar = self.table.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2

        if not ev.crc_ok:
            color = LOG_WARN
        elif p.kind in ("TEXT",) or p.kind.startswith("CHAN "):
            color = LOG_OK
        elif p.kind in ("ADVERT", "NODEINFO", "POSITION"):
            color = TXT
        else:
            color = DIM

        cells = (
            ev.time.strftime("%H:%M:%S"),
            f"{ev.rssi:.0f} dBm / {ev.snr:+.1f}",
            p.kind,
            p.sender,
            p.text,
            p.hops,
        )
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setForeground(QColor(ACCENT if col == 3 and p.sender else color))
            if col == 4:
                item.setToolTip(f"{text}\n\nraw: {ev.raw.hex()}")
            self.table.setItem(row, col, item)

        overflow = self.table.rowCount() - MAX_ROWS
        for _ in range(overflow):
            self.table.removeRow(0)
        if at_bottom:
            self.table.scrollToBottom()

    def clear(self):
        self.table.setRowCount(0)
