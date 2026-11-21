"""MESSAGES panel: every packet the app decodes, from MeshCore and
Meshtastic alike, whether or not it's something the node logger records.

Rows come from core/messages.py via the Bridge's `message` signal, and
core.messages.MessageLog does the storage and the folding of relayed
copies into one "×N" row; this file only displays it. Newest on top.

Two looks: the desktop one (redesign 1a -- filter field, Pause, painted
rows with a mesh badge and a signal mini-bar, selection feeding the packet
inspector) and compact=True for the 640x480 pocket layout.
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QStyle,
    QStyledItemDelegate,
    QMenu,
    QTableView,
    QToolButton,
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
from ..theme import (
    ACCENT_LIGHT,
    SWITCH_TRACK_ON,
    ACCENT,
    DIM,
    LINE_SOFT,
    LOG_WARN,
    MESH_MESHCORE,
    MESH_MESHTASTIC,
    ROW_HOVER,
    ROW_SELECTED,
    ROW_SEPARATOR,
    TERMINAL_BG,
    TXT,
    pick_mono_font,
    pick_sans_font,
)
from .common import make_button, section_label
from .toggle_switch import ToggleSwitch

# Radio/Profile are appended (so the other indices stay put) but shown
# after Time / Mesh, and only with several sniffer radios (redesign 1d).
COLUMNS = ("Time", "Mesh", "Channel", "From", "Message", "Signal", "RSSI · SNR", "Hops", "Radio", "Profile")
(COL_TIME, COL_MESH, COL_CHANNEL, COL_FROM, COL_MESSAGE, COL_SIGNAL, COL_RSSI, COL_HOPS, COL_RADIO,
 COL_PROFILE) = range(len(COLUMNS))
MESH_COLORS = {"meshcore": QColor(MESH_MESHCORE), "meshtastic": QColor(MESH_MESHTASTIC)}
MESH_SHORT = {"meshcore": "MC", "meshtastic": "MT"}
ROW_PAD = 12  # row padding left/right
CELL_GAP = 8
ROW_HEIGHT = 24  # single-line desktop row
MAX_LINES = 10  # taller messages are cut off here (the inspector shows all of it)


def message_lines(m):
    """The message text split into display lines, capped at MAX_LINES (the
    last kept line gets an ellipsis when more were dropped)."""
    lines = (m.get("text") or "").splitlines() or [""]
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES]
        lines[-1] += " …"
    return lines


class MessageModel(QAbstractTableModel):
    def __init__(self, parent=None, upper_headers=False):
        super().__init__(parent)
        self.log = MessageLog()
        self.upper_headers = upper_headers
        mono = pick_mono_font()
        self._font = QFont(mono)
        self._font.setPixelSize(12)
        self._bold = QFont(self._font)
        self._bold.setBold(True)
        self._italic = QFont(self._font)
        self._italic.setItalic(True)
        self._small_font = QFont(self._font)
        self._small_font.setPixelSize(11)
        self.radio_numbers = {}  # radio label -> 1-based number (multi-radio)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.log.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            text = "RSSI" if section == COL_RSSI and getattr(self, "small", False) else COLUMNS[section]
            return text.upper() if self.upper_headers else text
        if orientation == Qt.Horizontal and role == Qt.TextAlignmentRole and section == COL_HOPS:
            return int(Qt.AlignRight | Qt.AlignVCenter)
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
            if col == COL_RSSI:
                return "" if m.get("rssi") is None else f"{m['rssi']:.0f}"
            if col == COL_HOPS:
                return "" if m.get("hops") is None else str(m["hops"])
            if col == COL_RADIO:
                n = self.radio_numbers.get(m.get("radio") or "")
                return str(n) if n else ""
            if col == COL_PROFILE:
                return profile_text(m)
        elif role == Qt.ForegroundRole:
            if col == COL_MESH:
                return MESH_COLORS.get(m["mesh"], QColor(TXT))
            if col == COL_MESSAGE:
                return QColor(TXT if is_text else DIM)
            if col == COL_FROM and is_text:
                return QColor(ACCENT)
            return QColor(DIM if col in (COL_TIME, COL_SIGNAL, COL_RSSI, COL_HOPS, COL_PROFILE) else TXT)
        elif role == Qt.FontRole:
            if col == COL_PROFILE:
                return self._small_font
            if col == COL_MESH:
                return self._bold
            if col == COL_MESSAGE and m["kind"] == "encrypted":
                return self._italic
            return self._font
        elif role == Qt.TextAlignmentRole:
            if col == COL_HOPS:
                return int(Qt.AlignRight | Qt.AlignVCenter)
        elif role == Qt.ToolTipRole:
            bits = [f"{MESH_LABELS.get(m['mesh'], m['mesh'])} via {m['source']}"]
            if m["profile"]:
                bits.append(f"profile: {m['profile']}")
            signal = signal_text(m)
            if signal:
                bits.append(signal)
            if m.get("hops") is not None:
                bits.append(f"{m['hops']} hop{'' if m['hops'] == 1 else 's'}")
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
        self.text = ""

    def set_filter(self, mesh, text_only, channel=None, text=""):
        self.mesh, self.text_only, self.channel, self.text = mesh, text_only, channel, text
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        return matches(self.sourceModel().rows[source_row], self.mesh, self.text_only, self.channel,
                       self.text.strip())


def profile_text(m):
    """"MeshCore US · 910.525" from the profile name + label."""
    name = (m.get("profile") or "").replace("Meshtastic ", "MT ", 1)
    label = m.get("profile_label") or ""
    if " — " in label:
        freq = label.split(" — ", 1)[1].split(" MHz", 1)[0]
        return f"{name} · {freq}" if name else freq
    return name


def row_message(index):
    """The message dict behind a (proxy) index -- the live object, not a
    copy (returning a dict through data() would make PySide copy it)."""
    model = index.model()
    if isinstance(model, QSortFilterProxyModel):
        index = model.mapToSource(index)
        model = model.sourceModel()
    return model.rows[index.row()]


class MessageRowDelegate(QStyledItemDelegate):
    """Paints desktop rows: flat background (hover/selected), 1 px
    separator, padded elided text, the MC/MT badge and the signal bar."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.hover_row = -1
        self.small = False  # small window: RSSI as a bare number, mesh as channel colour
        self.floor = None  # current noise floor (dBm): a tick on each signal bar

    def paint(self, p, option, index):
        p.save()
        rect = option.rect
        selected = bool(option.state & QStyle.State_Selected)
        bg = ROW_SELECTED if selected else (ROW_HOVER if index.row() == self.hover_row else TERMINAL_BG)
        p.fillRect(rect, QColor(bg))
        p.fillRect(rect.left(), rect.bottom(), rect.width(), 1, QColor(ROW_SEPARATOR))

        col = index.column()
        model = index.model()
        first = col == self._first_visible()
        last = col == self._last_visible()
        left = rect.left() + (ROW_PAD if first else CELL_GAP // 2)
        right = rect.right() - (ROW_PAD if last else CELL_GAP // 2)
        # Every cell lines up with the first line of a multi-line message.
        inner = QRectF(left, rect.top(), max(0, right - left), min(rect.height(), ROW_HEIGHT))
        m = row_message(index)

        if col == COL_MESH:
            self._paint_badge(p, inner, m)
        elif col == COL_RADIO:
            self._paint_radio(p, inner, model.data(index, Qt.DisplayRole))
        elif col == COL_RSSI and not self.small:
            self._paint_signal(p, inner, m)
        else:
            font = model.data(index, Qt.FontRole)
            color = model.data(index, Qt.ForegroundRole)
            if col == COL_TIME and selected:
                color = QColor(ACCENT)
            elif self.small and col == COL_CHANNEL:
                color = MESH_COLORS.get(m.get("mesh"), QColor(TXT))
            p.setFont(font)
            p.setPen(color)
            align = model.data(index, Qt.TextAlignmentRole) or int(Qt.AlignLeft | Qt.AlignVCenter)
            fm = p.fontMetrics()
            if col == COL_MESSAGE:
                lines = message_lines(m)
            else:
                lines = [(model.data(index, Qt.DisplayRole) or "").replace("\n", " ")]
            line_rect = QRectF(inner)
            for i, line in enumerate(lines):
                if i:
                    line_rect.translate(0, fm.lineSpacing())
                p.drawText(line_rect, int(align), fm.elidedText(line, Qt.ElideRight, int(inner.width())))
        p.restore()

    def _first_visible(self):
        hdr = self.view.horizontalHeader()
        return hdr.logicalIndex(next(v for v in range(hdr.count()) if not hdr.isSectionHidden(hdr.logicalIndex(v))))

    def _last_visible(self):
        hdr = self.view.horizontalHeader()
        return hdr.logicalIndex(next(v for v in reversed(range(hdr.count()))
                                     if not hdr.isSectionHidden(hdr.logicalIndex(v))))

    def _paint_badge(self, p, inner, m):
        mesh = m.get("mesh")
        color = MESH_COLORS.get(mesh, QColor(TXT))
        font = QFont(pick_mono_font())
        font.setPixelSize(9)
        font.setBold(True)
        p.setFont(font)
        text = MESH_SHORT.get(mesh, "?")
        w = p.fontMetrics().horizontalAdvance(text) + 8
        box = QRectF(inner.left(), inner.center().y() - 7, w, 14)
        p.setRenderHint(p.RenderHint.Antialiasing)
        p.setPen(QPen(color, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        p.drawText(box, Qt.AlignCenter, text)

    def _paint_radio(self, p, inner, text):
        if not text:
            return
        box = QRectF(inner.left(), inner.center().y() - 9, 18, 18)
        p.setRenderHint(p.RenderHint.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(SWITCH_TRACK_ON))
        p.drawRoundedRect(box, 4, 4)
        font = QFont(pick_mono_font())
        font.setPixelSize(10)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(ACCENT_LIGHT))
        p.drawText(box, Qt.AlignCenter, text)

    def _paint_signal(self, p, inner, m):
        rssi, snr = m.get("rssi"), m.get("snr")
        track = QRectF(inner.left(), inner.center().y() - 2, 28, 4)
        p.setRenderHint(p.RenderHint.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(LINE_SOFT))
        p.drawRoundedRect(track, 2, 2)
        floor_frac = None if self.floor is None else min(1.0, max(0.0, (self.floor + 110) / 80))
        if rssi is not None:
            frac = min(1.0, max(0.0, (rssi + 110) / 80))
            p.setBrush(QColor(ACCENT if frac > 0.6 else LOG_WARN))
            # With a known floor the fill runs from the floor up to the RSSI,
            # so its length is the packet's margin over the noise.
            start = floor_frac if floor_frac is not None else 0.0
            if frac > start:  # at or below the floor: tick only
                p.drawRoundedRect(QRectF(track.left() + 28 * start, track.top(), 28 * (frac - start), 4), 2, 2)
        if floor_frac is not None:
            p.fillRect(QRectF(track.left() + 28 * floor_frac - 0.5, track.top() - 2, 1, 8), QColor(LOG_WARN))
        font = QFont(pick_mono_font())
        font.setPixelSize(12)
        p.setFont(font)
        x = track.right() + 6
        if rssi is not None:
            text = f"{rssi:.0f}"
            p.setPen(QColor(TXT))
            p.drawText(QRectF(x, inner.top(), inner.right() - x, inner.height()), Qt.AlignLeft | Qt.AlignVCenter, text)
            x += p.fontMetrics().horizontalAdvance(text) + 5
        if snr is not None and x < inner.right():
            p.setPen(QColor(DIM))
            p.drawText(QRectF(x, inner.top(), inner.right() - x, inner.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(f"{snr:.1f}", Qt.ElideRight, int(inner.right() - x)))


class MessageView(QTableView):
    """Tracks the hovered row for whole-row hover highlighting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.row_delegate = None

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self.row_delegate is not None:
            row = self.rowAt(event.position().toPoint().y())
            if row != self.row_delegate.hover_row:
                self.row_delegate.hover_row = row
                self.viewport().update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self.row_delegate is not None and self.row_delegate.hover_row != -1:
            self.row_delegate.hover_row = -1
            self.viewport().update()


class MessagePanel(QWidget):
    # Emitted with a list of "#name" strings the user chose to subscribe to
    # after a "Find channels" dictionary attack; the window saves them.
    channels_found = Signal(list)
    # The selected row's message dict (None when nothing is selected), and
    # the same row again when it folds in another relayed copy.
    message_selected = Signal(object)
    message_updated = Signal(object)

    def __init__(self, parent=None, compact=False):
        super().__init__(parent)
        self.setObjectName("messagePanel")
        self._mono = pick_mono_font()
        self._compact = compact
        self._subscribed_entries = []
        self._paused = False
        self._pending = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header_compact() if compact else self._build_header())
        layout.addWidget(self._build_body(compact), stretch=1)
        self._update_count()

    # ---- headers -------------------------------------------------------------

    def _build_header(self):
        header = QFrame()
        header.setObjectName("messageHeader")
        header.setFixedHeight(40)
        row = QHBoxLayout(header)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(8)
        sans = pick_sans_font()

        title = QLabel("MESSAGES")
        title.setStyleSheet(f"font-family: '{self._mono}'; font-size: 11px; font-weight: 600;"
                            f" letter-spacing: 0.1em; color: {ACCENT};")
        row.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"font-family: '{self._mono}'; font-size: 10px; color: {DIM};")
        row.addWidget(self.count_label)
        row.addStretch(1)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter sender or text…")
        self.filter_edit.setFixedSize(200, 26)
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setStyleSheet(f"font-family: '{self._mono}'; font-size: 11px;")
        self.filter_edit.textChanged.connect(self._apply_filter)
        row.addWidget(self.filter_edit)

        self._build_combos(row, height=26)

        self.text_only = ToggleSwitch()
        self.text_only.setToolTip("Only show chat/text messages")
        self.text_only.toggled.connect(self._apply_filter)
        row.addWidget(self.text_only)
        lbl = QLabel("Text only")
        lbl.setStyleSheet(f"font-family: '{sans}'; font-size: 11px; color: {TXT};")
        row.addWidget(lbl)

        for text, slot, attr in (("Find channels", self._find_channels, "find_button"),
                                 ("Pause", self.toggle_pause, "pause_button"),
                                 ("Clear", self.clear, "clear_button")):
            btn = make_button(text, variant="secondary", height=26)
            btn.setFixedHeight(26)
            btn.setStyleSheet(f"font-family: '{sans}'; font-size: 11px; font-weight: 500;")
            btn.clicked.connect(slot)
            setattr(self, attr, btn)
            row.addWidget(btn)
        self.find_button.setToolTip("Try to recover unknown #hashtag channels from captured traffic")
        self.pause_button.setToolTip("Freeze the list; new messages are kept and added on Resume")

        # Small window: Find channels / Pause / Clear move into a "⋯" menu.
        self.more_button = QToolButton()
        self.more_button.setObjectName("sentButton")
        self.more_button.setText("⋯")
        self.more_button.setFixedSize(30, 26)
        self.more_button.setCursor(Qt.PointingHandCursor)
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        more = QMenu(self.more_button)
        more.addAction("Find channels…", self._find_channels)
        self.pause_action = more.addAction("Pause", self.toggle_pause)
        more.addAction("Clear", self.clear)
        self.more_button.setMenu(more)
        self.more_button.setVisible(False)
        row.addWidget(self.more_button)
        self._header = header
        return header

    def set_noise_floor(self, dbm):
        """Current noise floor, marked on every row's signal bar (desktop)."""
        delegate = getattr(self.view, "row_delegate", None)
        if delegate is not None and delegate.floor != dbm:
            delegate.floor = None if dbm is None else round(dbm)
            self.view.viewport().update()

    def set_radio_numbers(self, numbers):
        """{radio label: number}; two or more radios show the Radio and
        Profile columns (desktop layout only)."""
        self.model.radio_numbers = dict(numbers)
        self._multi_radio = len(numbers) > 1 and not self._compact
        self._update_radio_columns()
        if self.model.rows:
            self.model.dataChanged.emit(self.model.index(0, COL_RADIO),
                                        self.model.index(len(self.model.rows) - 1, COL_PROFILE))

    def _update_radio_columns(self):
        small = getattr(self.view.row_delegate, "small", False) if hasattr(self.view, "row_delegate") else False
        self.view.setColumnHidden(COL_RADIO, not self._multi_radio)
        self.view.setColumnHidden(COL_PROFILE, not self._multi_radio or small)

    def set_small(self, small):
        """Small window (redesign 1c), desktop layout only: 36 px header
        with a "⋯" menu, and Time · Channel · From · Message · RSSI."""
        if self._compact:
            return
        self._header.setFixedHeight(36 if small else 40)
        for btn in (self.find_button, self.pause_button, self.clear_button):
            btn.setVisible(not small)
        self.more_button.setVisible(small)
        self.filter_edit.setFixedWidth(150 if small else 200)
        view = self.view
        view.row_delegate.small = small
        view.setColumnHidden(COL_MESH, small)
        view.setColumnHidden(COL_HOPS, small)
        if small:
            widths = {COL_TIME: 58 + ROW_PAD + CELL_GAP // 2, COL_CHANNEL: 96 + CELL_GAP,
                      COL_FROM: 120 + CELL_GAP, COL_RSSI: 40 + ROW_PAD + CELL_GAP // 2}
        else:
            widths = self._desktop_widths
        for col, w in widths.items():
            view.setColumnWidth(col, w)
        self.model.small = small
        self._update_radio_columns()
        self.model.headerDataChanged.emit(Qt.Horizontal, COL_RSSI, COL_RSSI)
        view.viewport().update()

    def _build_header_compact(self):
        header = QFrame()
        header.setObjectName("terminalHeader")
        header.setFixedHeight(44)
        row = QHBoxLayout(header)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(8)
        title = section_label("MESSAGES")
        title.setStyleSheet(title.styleSheet() + f" color: {ACCENT};")
        row.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"font-family: '{self._mono}'; font-size: 10px; color: {DIM};")
        row.addWidget(self.count_label)
        row.addStretch(1)
        self.filter_edit = None
        self._build_combos(row, height=None)
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
        self.pause_button = None
        return header

    def _build_combos(self, row, height):
        style = f"font-family: '{self._mono}'; font-size: 11px; padding: 2px 8px;"
        self.mesh_combo = QComboBox()
        self.mesh_combo.addItem("All meshes", None)
        self.mesh_combo.addItem("MeshCore", "meshcore")
        self.mesh_combo.addItem("Meshtastic", "meshtastic")
        self.mesh_combo.setStyleSheet(style)
        self.mesh_combo.currentIndexChanged.connect(self._apply_filter)
        row.addWidget(self.mesh_combo)

        # Filled from the subscribed channels (set_subscribed) plus any
        # channel a message has arrived on, so it grows as traffic comes in.
        self._subscribed = []
        self.channel_combo = QComboBox()
        self.channel_combo.addItem("All channels", None)
        self.channel_combo.addItem("DM", "DM")
        self.channel_combo.setToolTip("Only show messages on one channel")
        self.channel_combo.setStyleSheet(style)
        self.channel_combo.currentIndexChanged.connect(self._apply_filter)
        row.addWidget(self.channel_combo)
        if height:
            self.mesh_combo.setFixedHeight(height)
            self.channel_combo.setFixedHeight(height)

    # ---- body ----------------------------------------------------------------

    def _build_body(self, compact):
        self.model = MessageModel(self, upper_headers=not compact)
        self.proxy = MessageFilter(self)
        self.proxy.setSourceModel(self.model)
        self.model.rowsInserted.connect(self._update_count)
        self.model.modelReset.connect(self._update_count)
        self.model.dataChanged.connect(self._on_data_changed)

        view = MessageView()
        view.setObjectName("messages" if compact else "messageList")
        view.setModel(self.proxy)
        view.setSelectionBehavior(QAbstractItemView.SelectRows)
        view.setSelectionMode(QAbstractItemView.SingleSelection)
        view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        view.setShowGrid(False)
        view.setWordWrap(False)
        view.setAlternatingRowColors(False)
        view.verticalHeader().setVisible(False)
        view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        hdr = view.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.setMinimumSectionSize(20)
        if compact:
            hdr.setFont(QFont(self._mono))
            view.verticalHeader().setDefaultSectionSize(22)
            widths = {COL_TIME: 62, COL_MESH: 84, COL_CHANNEL: 80, COL_FROM: 110}
            hidden = (COL_SIGNAL, COL_RSSI, COL_HOPS)  # at 640 px the text needs the room
        else:
            hfont = QFont(self._mono)
            hfont.setPixelSize(10)
            hfont.setLetterSpacing(QFont.PercentageSpacing, 104)
            hdr.setFont(hfont)
            hdr.setFixedHeight(24)
            view.verticalHeader().setDefaultSectionSize(24)
            view.verticalHeader().setMinimumSectionSize(24)
            # +ROW_PAD on the outer columns, +CELL_GAP between the rest.
            widths = {COL_TIME: 62 + ROW_PAD + CELL_GAP // 2, COL_MESH: 34 + CELL_GAP,
                      COL_CHANNEL: 104 + CELL_GAP, COL_FROM: 158 + CELL_GAP,
                      COL_RSSI: 92 + CELL_GAP, COL_HOPS: 50 + ROW_PAD + CELL_GAP // 2}
            hidden = (COL_SIGNAL,)
            self._desktop_widths = dict(widths)
            view.row_delegate = MessageRowDelegate(view)
            view.setItemDelegate(view.row_delegate)
            hdr.setStyleSheet(
                f"QHeaderView::section {{ font-family: '{self._mono}'; font-size: 10px; letter-spacing: 0.04em;"
                f" padding-left: {CELL_GAP // 2}px; padding-right: {CELL_GAP // 2}px; }}"
                f" QHeaderView::section:first {{ padding-left: {ROW_PAD}px; }}"
                f" QHeaderView::section:last {{ padding-right: {ROW_PAD}px; }}")
        for col, w in widths.items():
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
            view.setColumnWidth(col, w)
        hdr.setSectionResizeMode(COL_MESSAGE, QHeaderView.Stretch)
        hidden = tuple(hidden) + (COL_RADIO, COL_PROFILE)
        for col in hidden:
            view.setColumnHidden(col, True)
        if not compact:
            for col, w in ((COL_RADIO, 44 + CELL_GAP), (COL_PROFILE, 180 + CELL_GAP)):
                hdr.setSectionResizeMode(col, QHeaderView.Interactive)
                view.setColumnWidth(col, w)
            hdr.moveSection(hdr.visualIndex(COL_RADIO), hdr.visualIndex(COL_TIME) + 1)
            hdr.moveSection(hdr.visualIndex(COL_PROFILE), hdr.visualIndex(COL_MESH) + 1)
        self._multi_radio = False
        view.selectionModel().currentRowChanged.connect(self._on_current_changed)
        self.view = view
        self._row_height = 22 if compact else ROW_HEIGHT
        self.proxy.rowsInserted.connect(lambda _parent, first, last: self._fit_rows(first, last))
        self.proxy.layoutChanged.connect(lambda *_: self._fit_rows())
        self.proxy.modelReset.connect(lambda: self._fit_rows())
        self.proxy.dataChanged.connect(lambda tl, br, *_: self._fit_rows(tl.row(), br.row()))
        return view

    def _fit_rows(self, first=0, last=None):
        """Grow rows whose message has line breaks so every line shows
        (rows are a fixed single-line height otherwise)."""
        if last is None:
            last = self.proxy.rowCount() - 1
        line = QFontMetrics(self.model._font).lineSpacing()
        for row in range(first, last + 1):
            m = row_message(self.proxy.index(row, 0))
            extra = len(message_lines(m)) - 1
            height = self._row_height + extra * line
            if self.view.rowHeight(row) != height:
                self.view.setRowHeight(row, height)

    # ---- selection -------------------------------------------------------------

    def selected_message(self):
        idx = self.view.selectionModel().currentIndex()
        if not idx.isValid() or not self.view.selectionModel().isRowSelected(idx.row(), QModelIndex()):
            return None
        return row_message(idx)

    def _on_current_changed(self, current, _previous):
        self.message_selected.emit(row_message(current) if current.isValid() else None)

    def _on_data_changed(self, top_left, bottom_right, _roles=()):
        current = self.view.selectionModel().currentIndex()
        if not current.isValid():
            return
        source_row = self.proxy.mapToSource(current).row()
        if top_left.row() <= source_row <= bottom_right.row():
            self.message_updated.emit(self.model.rows[source_row])

    def clear_selection(self):
        self.view.selectionModel().clearCurrentIndex()
        self.view.clearSelection()

    # ---- pause -----------------------------------------------------------------

    def toggle_pause(self):
        self.set_paused(not self._paused)

    def set_paused(self, paused):
        self._paused = paused
        if not paused:
            pending, self._pending = self._pending, []
            for m in pending:
                self._add(m)
        self._update_pause_label()

    def is_paused(self):
        return self._paused

    def _update_pause_label(self):
        if self.pause_button is None:
            return
        text = f"Resume ({len(self._pending)} new)" if self._paused else "Pause"
        self.pause_button.setText(text)
        if getattr(self, "pause_action", None) is not None:
            self.pause_action.setText(text)
            self.more_button.setText("⋯ ⏸" if self._paused else "⋯")
            self.more_button.setFixedWidth(44 if self._paused else 30)

    # ---- filtering ---------------------------------------------------------------

    def _apply_filter(self, *_):
        text = self.filter_edit.text() if self.filter_edit is not None else ""
        self.proxy.set_filter(
            self.mesh_combo.currentData(), self.text_only.isChecked(), self.channel_combo.currentData(), text,
        )
        self._update_count()

    def _update_count(self, *_):
        total = len(self.model.rows)
        shown = self.proxy.rowCount()
        if self._compact:
            self.count_label.setText(f"{total}" if shown == total else f"{shown}/{total}")
        else:
            self.count_label.setText(f"{total}" if shown == total else f"{shown} / {total}")

    # ---- feed -----------------------------------------------------------------------

    def add_message(self, m):
        if self._paused:
            self._pending.append(m)
            self._update_pause_label()
            return
        self._add(m)

    def _add(self, m):
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

    def _find_channels(self):
        chosen = self.crack_channels(self._subscribed_entries, parent=self)
        if chosen:
            self.channels_found.emit(chosen)

    def crack_channels(self, entries, parent=None):
        """Try to recover unknown #hashtag channels from the captured
        traffic (skipping those in `entries`); shows the results dialog and
        returns the names the user picked."""
        from ...core import channel_crack as cc
        from ...core.channels import subscribed_channels
        from .crack_dialog import CrackResultsDialog

        rows = self.model.rows
        payloads = cc.captured_payloads(rows)
        known = {c["hash"] for c in subscribed_channels({"meshcore_channels": entries})}
        names = cc.candidate_names(extra=cc.words_from_messages(rows))
        results = cc.crack(payloads, names, known_hashes=known)
        dialog = CrackResultsDialog(results, len(payloads), parent or self)
        if dialog.exec() and results:
            return dialog.chosen_names()
        return []

    def channel_stats(self):
        """({channel name lowercased: message count}, number of distinct
        unknown channel hashes) over this session's rows."""
        counts, unknown = {}, set()
        for m in self.model.rows:
            name = channel_text(m)
            if not name:
                continue
            if name.startswith("hash "):
                unknown.add(name)
            else:
                counts[name.lower()] = counts.get(name.lower(), 0) + 1
        return counts, len(unknown)

    def clear(self):
        self.model.clear()
        self._pending = []
        self._update_pause_label()
        self.message_selected.emit(None)
