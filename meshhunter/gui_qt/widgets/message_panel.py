"""MESSAGES panel: every packet the app decodes, from MeshCore and
Meshtastic alike, whether or not it's something the node logger records.

Rows come from core/messages.py via the Bridge's `message` signal, and
core.messages.MessageLog does the storage and the folding of relayed
copies into one "×N" row; this file only displays it. Newest on top.
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...core.messages import (
    MESH_LABELS,
    TEXT_KINDS,
    MessageLog,
    channel_choices,
    channel_text,
    from_text,
    matches,
    signal_text,
)
from ..theme import ACCENT, DIM, MESH_MESHCORE, MESH_MESHTASTIC, TERMINAL_DIVIDER, TXT, pick_mono_font
from .common import make_button, section_label
from .toggle_switch import ToggleSwitch

COLUMNS = ("Time", "Mesh", "Channel", "From", "Message", "Signal")
COL_TIME, COL_MESH, COL_CHANNEL, COL_FROM, COL_MESSAGE, COL_SIGNAL = range(len(COLUMNS))
MESH_COLORS = {"meshcore": QColor(MESH_MESHCORE), "meshtastic": QColor(MESH_MESHTASTIC)}


class MessageModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.log = MessageLog()
        mono = pick_mono_font()
        self._font = QFont(mono)
        self._font.setPixelSize(12)
        self._bold = QFont(self._font)
        self._bold.setBold(True)
        self._italic = QFont(self._font)
        self._italic.setItalic(True)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.log.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        m = self.log.rows[index.row()]
        col = index.column()
        is_text = m["kind"] in TEXT_KINDS
        if role == Qt.DisplayRole:
            if col == COL_TIME:
                return m["time"]
            if col == COL_MESH:
                return MESH_LABELS.get(m["mesh"], m["mesh"])
            if col == COL_CHANNEL:
                return channel_text(m)
            if col == COL_FROM:
                return from_text(m)
            if col == COL_MESSAGE:
                return m["text"]
            if col == COL_SIGNAL:
                return signal_text(m)
        elif role == Qt.ForegroundRole:
            if col == COL_MESH:
                return MESH_COLORS.get(m["mesh"], QColor(TXT))
            if col == COL_MESSAGE:
                return QColor(TXT if is_text else DIM)
            if col == COL_FROM and is_text:
                return QColor(ACCENT)
            return QColor(DIM if col in (COL_TIME, COL_SIGNAL) else TXT)
        elif role == Qt.FontRole:
            if col == COL_MESH:
                return self._bold
            if col == COL_MESSAGE and m["kind"] == "encrypted":
                return self._italic
            return self._font
        elif role == Qt.ToolTipRole:
            bits = [f"{MESH_LABELS.get(m['mesh'], m['mesh'])} via {m['source']}"]
            if m["profile"]:
                bits.append(f"profile: {m['profile']}")
            signal = signal_text(m)
            if signal:
                bits.append(signal)
            bits.append(m["text"])
            return "\n".join(bits)
        return None

    def add(self, m):
        index = self.log.find(m)
        if index is not None:
            self.log.merge(index, m)
            self.dataChanged.emit(self.index(index, 0), self.index(index, len(COLUMNS) - 1))
            return
        self.beginInsertRows(QModelIndex(), 0, 0)
        self.log.insert(m)
        self.endInsertRows()
        extra = self.log.overflow()
        if extra:
            total = len(self.log.rows)
            self.beginRemoveRows(QModelIndex(), total - extra, total - 1)
            self.log.trim()
            self.endRemoveRows()

    def clear(self):
        self.beginResetModel()
        self.log.clear()
        self.endResetModel()

    @property
    def rows(self):
        return self.log.rows


class MessageFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mesh = None
        self.text_only = False
        self.channel = None

    def set_filter(self, mesh, text_only, channel=None):
        self.mesh, self.text_only, self.channel = mesh, text_only, channel
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        return matches(self.sourceModel().rows[source_row], self.mesh, self.text_only, self.channel)


class MessagePanel(QWidget):
    # Emitted with a list of "#name" strings the user chose to subscribe to
    # after a "Find channels" dictionary attack; the window saves them.
    channels_found = Signal(list)

    def __init__(self, parent=None, compact=False):
        super().__init__(parent)
        self.setObjectName("messagePanel")
        self._mono = pick_mono_font()
        self._subscribed_entries = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header(compact))
        layout.addWidget(self._build_body(compact), stretch=1)
        self._update_count()

    def _build_header(self, compact):
        header = QFrame()
        header.setObjectName("terminalHeader")
        header.setFixedHeight(44)
        row = QHBoxLayout(header)
        row.setContentsMargins(16 if not compact else 10, 0, 16 if not compact else 10, 0)
        row.setSpacing(8)

        title = section_label("MESSAGES")
        title.setStyleSheet(title.styleSheet() + f" color: {ACCENT};")
        row.addWidget(title)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"font-family: '{self._mono}'; font-size: 10px; color: {DIM};")
        row.addWidget(self.count_label)

        if not compact:
            divider = QFrame()
            divider.setFrameShape(QFrame.HLine)
            divider.setStyleSheet(f"background-color: {TERMINAL_DIVIDER}; max-height: 1px; border: none;")
            row.addWidget(divider, stretch=1)
        else:
            row.addStretch(1)

        self.mesh_combo = QComboBox()
        self.mesh_combo.addItem("All meshes", None)
        self.mesh_combo.addItem("MeshCore", "meshcore")
        self.mesh_combo.addItem("Meshtastic", "meshtastic")
        self.mesh_combo.setStyleSheet(f"font-family: '{self._mono}'; font-size: 11px; padding: 2px 8px;")
        self.mesh_combo.currentIndexChanged.connect(self._apply_filter)
        row.addWidget(self.mesh_combo)

        # Filled from the subscribed channels (set_subscribed) plus any
        # channel a message has arrived on, so it grows as traffic comes in.
        self._subscribed = []
        self.channel_combo = QComboBox()
        self.channel_combo.addItem("All channels", None)
        self.channel_combo.addItem("DM", "DM")
        self.channel_combo.setToolTip("Only show messages on one channel")
        self.channel_combo.setStyleSheet(f"font-family: '{self._mono}'; font-size: 11px; padding: 2px 8px;")
        self.channel_combo.currentIndexChanged.connect(self._apply_filter)
        row.addWidget(self.channel_combo)

        self.text_only = ToggleSwitch()
        self.text_only.setToolTip("Only show chat/text messages")
        self.text_only.toggled.connect(self._apply_filter)
        row.addWidget(self.text_only)
        lbl = QLabel("Text only")
        lbl.setStyleSheet(f"font-family: '{self._mono}'; font-size: 11px; color: {TXT};")
        row.addWidget(lbl)

        find_btn = make_button("Find channels", variant="secondary", height=26)
        find_btn.setToolTip("Try to recover unknown #hashtag channels from captured traffic")
        find_btn.clicked.connect(self._find_channels)
        row.addWidget(find_btn)

        clear_btn = make_button("Clear", variant="secondary", height=26)
        clear_btn.clicked.connect(self.clear)
        row.addWidget(clear_btn)
        return header

    def _find_channels(self):
        from ...core import channel_crack as cc
        from ...core.channels import subscribed_channels
        from .crack_dialog import CrackResultsDialog

        rows = self.model.rows
        payloads = cc.captured_payloads(rows)
        known = {c["hash"] for c in subscribed_channels({"meshcore_channels": self._subscribed_entries})}
        names = cc.candidate_names(extra=cc.words_from_messages(rows))
        results = cc.crack(payloads, names, known_hashes=known)
        dialog = CrackResultsDialog(results, len(payloads), self)
        if dialog.exec() and results:
            chosen = dialog.chosen_names()
            if chosen:
                self.channels_found.emit(chosen)

    def _build_body(self, compact):
        self.model = MessageModel(self)
        self.proxy = MessageFilter(self)
        self.proxy.setSourceModel(self.model)
        self.model.rowsInserted.connect(self._update_count)
        self.model.modelReset.connect(self._update_count)

        view = QTableView()
        view.setObjectName("messages")
        view.setModel(self.proxy)
        view.setSelectionBehavior(QAbstractItemView.SelectRows)
        view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        view.setShowGrid(False)
        view.setWordWrap(False)
        view.setAlternatingRowColors(False)
        view.verticalHeader().setVisible(False)
        view.verticalHeader().setDefaultSectionSize(22)
        view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        hdr = view.horizontalHeader()
        hdr.setFont(QFont(self._mono))
        hdr.setHighlightSections(False)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        widths = {COL_TIME: 66, COL_MESH: 88, COL_CHANNEL: 110, COL_FROM: 150, COL_SIGNAL: 170}
        if compact:
            widths = {COL_TIME: 62, COL_MESH: 84, COL_CHANNEL: 80, COL_FROM: 110, COL_SIGNAL: 150}
        for col, w in widths.items():
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
            view.setColumnWidth(col, w)
        hdr.setSectionResizeMode(COL_MESSAGE, QHeaderView.Stretch)
        if compact:
            # At 640 px the message text needs the room; signal is in the tooltip.
            view.setColumnHidden(COL_SIGNAL, True)
        self.view = view
        return view

    def _apply_filter(self, *_):
        self.proxy.set_filter(
            self.mesh_combo.currentData(), self.text_only.isChecked(), self.channel_combo.currentData()
        )
        self._update_count()

    def _update_count(self, *_):
        total = len(self.model.rows)
        shown = self.proxy.rowCount()
        self.count_label.setText(f"{total}" if shown == total else f"{shown}/{total}")

    def add_message(self, m):
        self.model.add(m)
        name = channel_text(m)
        if name and self.channel_combo.findData(name) < 0:
            self._refresh_channels()

    def set_subscribed(self, entries):
        """The channel subscription entries ({"name", "key"?, "enabled"}),
        for the channel filter and for excluding already-known channels from
        "Find channels"."""
        self._subscribed_entries = [dict(e) for e in entries]
        self._subscribed = [e["name"] for e in self._subscribed_entries if e.get("enabled", True)]
        self._refresh_channels()

    def _refresh_channels(self):
        current = self.channel_combo.currentData()
        wanted = channel_choices(self.model.rows, self._subscribed)
        self.channel_combo.blockSignals(True)
        while self.channel_combo.count() > 2:  # keep "All channels" and "DM"
            self.channel_combo.removeItem(2)
        for name in wanted:
            self.channel_combo.addItem(name, name)
        idx = self.channel_combo.findData(current)
        self.channel_combo.setCurrentIndex(max(0, idx))
        self.channel_combo.blockSignals(False)
        if idx < 0 and current is not None:
            self._apply_filter()

    def clear(self):
        self.model.clear()
