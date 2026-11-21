"""The RF band's Sensors view: one row per device rtl_433 has decoded (see
core/rf_sensors.py), newest first, with its latest readings."""

import calendar
import json
import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QStackedWidget, QTableView

from ...core.rf_sensors import classify
from ..theme import ACCENT, ACCENT_LIGHT, DIM, LOG_WARN, TXT, pick_mono_font

COLUMNS = ["Type", "Model", "ID", "Readings", "RSSI", "SNR", "Heard", "Last seen", "MHz"]
COL_TYPE, COL_MODEL, COL_ID, COL_READINGS, COL_RSSI, COL_SNR, COL_HEARD, COL_LAST, COL_MHZ = range(9)
WIDTHS = {COL_TYPE: 76, COL_MODEL: 190, COL_ID: 110, COL_RSSI: 54, COL_SNR: 46, COL_HEARD: 52,
          COL_LAST: 74, COL_MHZ: 64}

CATEGORY_COLORS = {
    "Meter": "#ffb347", "Weather": "#5ec8ff", "TPMS": "#ff7878", "Security": "#ff8fd0",
    "Doorbell": "#c79bff", "Remote": "#ffe14d", "Leak": "#4fd1c5", "Other": DIM,
}
FLASH_S = 1.5


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return float("-inf")


def _ago(iso):
    """'12 s' / '4 min' / '2 h' since an ISO-8601 UTC time."""
    try:
        then = calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return ""
    s = max(0, time.time() - then)
    if s < 60:
        return f"{s:.0f} s"
    if s < 3600:
        return f"{s / 60:.0f} min"
    if s < 86400:
        return f"{s / 3600:.0f} h"
    return f"{s / 86400:.0f} d"


class SensorModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []  # SensorLog rows (dicts), this session's devices
        self._index = {}  # key -> row number
        self._flash = {}  # key -> monotonic time of last update
        self._mono = QFont(pick_mono_font())
        self._mono.setPixelSize(11)
        self._header_font = QFont(pick_mono_font())
        self._header_font.setPixelSize(10)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        if role == Qt.DisplayRole:
            return COLUMNS[section].upper()
        if role == Qt.FontRole:
            return self._header_font
        if role == Qt.TextAlignmentRole:
            right = section in (COL_RSSI, COL_SNR, COL_HEARD, COL_MHZ)
            return int((Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter)
        return None

    def upsert(self, row):
        key = row["key"]
        self._flash[key] = time.monotonic()
        if key in self._index:
            n = self._index[key]
            self.rows[n] = dict(row)
            self.dataChanged.emit(self.index(n, 0), self.index(n, len(COLUMNS) - 1))
        else:
            self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows))
            self._index[key] = len(self.rows)
            self.rows.append(dict(row))
            self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self.rows, self._index, self._flash = [], {}, {}
        self.endResetModel()

    def refresh_times(self):
        if self.rows:
            self.dataChanged.emit(self.index(0, COL_LAST), self.index(len(self.rows) - 1, COL_LAST))

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            return self._text(row, col)
        if role == Qt.UserRole:  # sort key
            return {COL_RSSI: _num(row.get("last_rssi")), COL_SNR: _num(row.get("last_snr")),
                    COL_HEARD: _num(row.get("count")), COL_MHZ: _num(row.get("freq_mhz")),
                    COL_LAST: row.get("last_seen", "")}.get(col, self._text(row, col).lower())
        if role == Qt.FontRole:
            return self._mono
        if role == Qt.ForegroundRole:
            if col == COL_TYPE:
                return QColor(CATEGORY_COLORS.get(row.get("category") or classify(row.get("model")), DIM))
            if col == COL_READINGS:
                return QColor(ACCENT_LIGHT if time.monotonic() - self._flash.get(row["key"], 0) < FLASH_S else TXT)
            if col == COL_RSSI:
                rssi = _num(row.get("last_rssi"))
                return QColor(ACCENT if rssi > -12 else LOG_WARN if rssi > -20 else DIM)
            if col in (COL_MODEL, COL_READINGS):
                return QColor(TXT)
            return QColor(DIM)
        if role == Qt.TextAlignmentRole and col in (COL_RSSI, COL_SNR, COL_HEARD, COL_MHZ):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            try:
                raw = json.dumps(json.loads(row.get("last_json") or "{}"), indent=1)
            except ValueError:
                raw = row.get("last_json", "")
            where = f"\nbest signal at {row['lat']}, {row['lon']}" if row.get("lat") else ""
            return (f"{row.get('model')}  id {row.get('id')}\nfirst heard {row.get('first_seen')} UTC"
                    f"{where}\n\n{raw}")
        return None

    @staticmethod
    def _text(row, col):
        if col == COL_TYPE:
            return row.get("category") or classify(row.get("model"))
        if col == COL_MODEL:
            return row.get("model", "")
        if col == COL_ID:
            ch = row.get("channel")
            return f"{row.get('id', '')}" + (f" ch{ch}" if ch not in ("", None) else "")
        if col == COL_READINGS:
            return row.get("readings", "")
        if col == COL_RSSI:
            return row.get("last_rssi", "")
        if col == COL_SNR:
            return row.get("last_snr", "")
        if col == COL_HEARD:
            return row.get("count", "")
        if col == COL_LAST:
            return _ago(row.get("last_seen", ""))
        if col == COL_MHZ:
            return row.get("freq_mhz", "")
        return ""


class SensorTable(QStackedWidget):
    """The device table, or an empty-state hint until something decodes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = SensorModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.UserRole)
        self.view = QTableView()
        self.view.setObjectName("sensorList")
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(COL_LAST, Qt.DescendingOrder)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        self.view.setTextElideMode(Qt.ElideRight)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(22)
        hdr = self.view.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        for col, width in WIDTHS.items():
            hdr.resizeSection(col, width)
        hdr.setSectionResizeMode(COL_READINGS, QHeaderView.Stretch)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hfont = QFont(pick_mono_font())
        hfont.setPixelSize(10)
        hdr.setFont(hfont)
        hdr.setFixedHeight(24)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"font-family: '{pick_mono_font()}'; font-size: 11px; color: {DIM};")
        self.set_empty_text("Listening… sensors appear here as they transmit.")
        self.addWidget(self.empty)
        self.addWidget(self.view)
        # Keeps "last seen" ticking and ends the update highlight.
        self._tick = QTimer(self)
        self._tick.timeout.connect(self.model.refresh_times)
        self._tick.timeout.connect(self.view.viewport().update)
        self._tick.start(1000)

    def set_empty_text(self, text):
        self.empty.setText(text)

    def add(self, row):
        self.model.upsert(row)
        self.setCurrentWidget(self.view)

    def device_count(self):
        return len(self.model.rows)

    def clear(self):
        self.model.clear()
        self.setCurrentWidget(self.empty)
