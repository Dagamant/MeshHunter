"""Settings > LoRa sniffer: what the sniffer listens on, and how it hops.

Only matters when the connected device is a MeshHunter sniffer (see
core/sniffer_worker.py), but always available so it can be set up before
plugging one in. Changes apply live to a connected sniffer after Save.

Hop mode shows a profile table with each profile's estimated catch rate
for first transmissions, a bar of one hop cycle, and the timing knobs.
Fixed mode shows only the profile picker.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.sniffer_profiles import (
    DEFAULT_FIXED_PROFILE,
    DEFAULT_HOP_PROFILES,
    MESHCORE,
    MESHTASTIC,
    all_profiles,
)
from ..theme import (
    ACCENT,
    ACCENT_LIGHT,
    CHECK_OFF_BORDER,
    ACCENT_DIM,
    DIM,
    LINE,
    LINE_SOFT,
    LOG_WARN,
    MESH_MESHCORE,
    MESH_MESHTASTIC,
    PANEL,
    PROFILE_ROW_ON,
    ROW_HOVER,
    SWITCH_TRACK_ON,
    TXT,
    pick_mono_font,
)
from .settings_common import Choice, SettingsPage, caption, hint, label, mono_style

_MODES = [("hop", "Hop between profiles"), ("fixed", "Fixed on one profile")]
COL_CHECK, COL_NAME, COL_MHZ, COL_BW, COL_SF, COL_DWELL, COL_CATCH = range(7)
_HEADERS = ["", "PROFILE", "MHZ", "BW", "SF", "DWELL", "CATCH"]
_WIDTHS = {COL_CHECK: 26, COL_MHZ: 78, COL_BW: 50, COL_SF: 38, COL_DWELL: 58, COL_CATCH: 62}
RETUNE_MS = 2  # firmware retune cost per hop


def family_color(profile):
    return QColor({MESHCORE: MESH_MESHCORE, MESHTASTIC: MESH_MESHTASTIC}.get(profile.family, DIM))


def short_name(profile):
    """"MC", "LF", "MF"...: the hop-cycle bar's segment labels."""
    if profile.family == MESHCORE:
        return "MC"
    rest = profile.name.replace("Meshtastic", "").strip() or profile.name
    caps = "".join(c for c in rest if c.isupper())
    return caps[:3] if len(caps) >= 2 else rest[:2].upper()


def catch_rate(profile, dwell, cycle):
    """Share of first transmissions caught: the radio must land on the
    profile while >= 6 of the 16 preamble symbols are still to come."""
    sym = profile.symbol_ms()
    return min(1.0, max(0.0, (16 * sym - 6 * sym + dwell) / cycle)) if cycle else 0.0


def _spin(maximum, step=250, special=None):
    box = QSpinBox()
    box.setRange(0, maximum)
    box.setSingleStep(step)
    box.setSuffix(" ms")
    box.setMinimumHeight(30)
    box.setStyleSheet(mono_style(12))
    if special:
        box.setSpecialValueText(special)
    return box


class _RowDelegate(QStyledItemDelegate):
    """Profile table rows: PROFILE_ROW_ON background when ticked, painted
    check box, family dot before the name, DIM text when unticked."""

    def __init__(self, panel):
        super().__init__(panel.table)
        self.panel = panel
        self.font = QFont(pick_mono_font())
        self.font.setPixelSize(12)

    def paint(self, p, option, index):
        p.save()
        row, col = index.row(), index.column()
        on = self.panel.is_checked(row)
        rect = QRectF(option.rect)
        hover = bool(option.state & QStyle.State_MouseOver)
        p.fillRect(rect, QColor(PROFILE_ROW_ON if on else (ROW_HOVER if hover else PANEL)))
        p.fillRect(QRectF(rect.left(), rect.bottom(), rect.width(), 1), QColor(LINE_SOFT))
        p.setRenderHint(QPainter.Antialiasing)
        if col == COL_CHECK:
            box = QRectF(rect.left() + 8, rect.center().y() - 7, 14, 14)
            p.setPen(QPen(QColor(ACCENT_DIM if on else CHECK_OFF_BORDER), 1))
            p.setBrush(QColor(SWITCH_TRACK_ON) if on else Qt.NoBrush)
            p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
            if on:
                p.setPen(QPen(QColor(ACCENT), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                o = box.topLeft()
                p.drawPolyline([o + QPointF(x, y) for x, y in ((3.5, 7.5), (6.1, 10.1), (10.9, 4.7))])
            p.restore()
            return
        p.setFont(self.font)
        text = index.data() or ""
        inner = rect.adjusted(8, 0, -10, 0)
        if col == COL_NAME:
            profile = self.panel.profiles[row]
            p.setPen(Qt.NoPen)
            p.setBrush(family_color(profile))
            p.drawEllipse(QRectF(inner.left(), rect.center().y() - 3, 6, 6))
            inner = inner.adjusted(14, 0, 0, 0)
        color = QColor(TXT if on else DIM)
        align = Qt.AlignLeft | Qt.AlignVCenter
        if col == COL_CATCH:
            align = Qt.AlignRight | Qt.AlignVCenter
            if on and text:
                color = QColor(ACCENT if self.panel.catch_of(row) > 0.6 else LOG_WARN)
        p.setPen(color)
        p.drawText(inner, align, p.fontMetrics().elidedText(text, Qt.ElideRight, int(inner.width())))
        p.restore()


class CycleBar(QWidget):
    """One hop cycle: a segment per ticked profile, width ∝ dwell + retune."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self._segments = []  # (profile, dwell)
        self._font = QFont(pick_mono_font())
        self._font.setPixelSize(10)

    def set_segments(self, segments):
        self._segments = segments
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        total = sum(d + RETUNE_MS for _, d in self._segments)
        if not total:
            p.setPen(QPen(QColor(LINE), 1, Qt.DashLine))
            p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 4, 4)
            return
        p.setFont(self._font)
        x = 0.0
        w = self.width() - 1
        for profile, dwell in self._segments:
            seg_w = w * (dwell + RETUNE_MS) / total
            r = QRectF(x + 0.5, 0.5, max(1.0, seg_w - 1), self.height() - 1)
            color = family_color(profile)
            fill = QColor(color)
            fill.setAlphaF(0.10)
            p.setPen(QPen(color, 1))
            p.setBrush(fill)
            p.drawRect(r)
            text = f"{short_name(profile)} {dwell}"
            if p.fontMetrics().horizontalAdvance(text) + 8 > r.width():
                text = short_name(profile)
            if p.fontMetrics().horizontalAdvance(text) + 6 <= r.width():
                p.setPen(color)
                p.drawText(r.adjusted(5, 0, -3, 0), Qt.AlignLeft | Qt.AlignVCenter, text)
            x += seg_w


class SnifferPanel(SettingsPage):
    TITLE = "LoRa sniffer"
    NAV_TITLE = "LoRa sniffer"
    DESCRIPTION = "What the sniffer's radio listens on. Changes apply live to a connected sniffer."

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.profiles = all_profiles(config)

        self.mode = Choice(_MODES, height=32)
        self.mode.changed.connect(self._on_mode)
        self.body.addWidget(self.mode)

        # ---- fixed
        self.fixed_widget = QWidget()
        fixed = QVBoxLayout(self.fixed_widget)
        fixed.setContentsMargins(0, 0, 0, 0)
        fixed.setSpacing(8)
        self.fixed_combo = QComboBox()
        self.fixed_combo.setMinimumHeight(32)
        self.fixed_combo.setMaximumWidth(460)
        for p in self.profiles:
            self.fixed_combo.addItem(p.label(), p.key)
        self.fixed_combo.currentIndexChanged.connect(lambda _: self.changed.emit())
        fixed.addWidget(self.fixed_combo)
        fixed.addWidget(hint("Listens continuously on one profile: hears every packet on it, nothing else."))
        self.body.addWidget(self.fixed_widget)

        # ---- hop
        self.hop_widget = QWidget()
        hop = QVBoxLayout(self.hop_widget)
        hop.setContentsMargins(0, 0, 0, 0)
        hop.setSpacing(10)
        self.table = QTableWidget(len(self.profiles), len(_HEADERS))
        self.table.setObjectName("profileTable")
        self.table.setStyleSheet(
            f"#profileTable {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px; }}"
            f" #profileTable QHeaderView::section {{ background: {PANEL}; color: {DIM}; border: none;"
            f" border-bottom: 1px solid {LINE}; font-family: '{pick_mono_font()}'; font-size: 10px;"
            f" padding: 0 8px; }}")
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.horizontalHeader().setFixedHeight(26)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.horizontalHeaderItem(COL_CATCH).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setMouseTracking(True)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setFixedHeight(26 + 28 * len(self.profiles) + 2)
        hdr = self.table.horizontalHeader()
        for col, w in _WIDTHS.items():
            hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            self.table.setColumnWidth(col, w)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self._checked = [False] * len(self.profiles)
        for row, prof in enumerate(self.profiles):
            for col, text in ((COL_NAME, prof.name), (COL_MHZ, f"{prof.freq:.3f}"), (COL_BW, f"{prof.bw:g}"),
                              (COL_SF, str(prof.sf))):
                item = QTableWidgetItem(text)
                item.setToolTip(prof.label())
                self.table.setItem(row, col, item)
            self.table.setItem(row, COL_DWELL, QTableWidgetItem(""))
            self.table.setItem(row, COL_CATCH, QTableWidgetItem(""))
            self.table.setItem(row, COL_CHECK, QTableWidgetItem(""))
        self.table.setItemDelegate(_RowDelegate(self))
        self.table.cellClicked.connect(lambda row, _col: self.set_checked(row, not self._checked[row]))
        hop.addWidget(self.table)

        cyc_head = QHBoxLayout()
        cyc_head.addWidget(caption("ONE HOP CYCLE"))
        cyc_head.addStretch(1)
        self.cycle_label = label("", mono_style(11, ACCENT_LIGHT))
        cyc_head.addWidget(self.cycle_label)
        hop.addLayout(cyc_head)
        self.cycle_bar = CycleBar()
        hop.addWidget(self.cycle_bar)
        hop.addWidget(hint("Repeats are caught more often than first transmissions, because the radio "
                           "stays on a profile after hearing traffic."))

        timing = QGridLayout()
        timing.setHorizontalSpacing(8)
        timing.setVerticalSpacing(4)
        self.dwell_spin = _spin(5000, step=10, special="Auto")
        self.linger_spin = _spin(60000)
        self.partial_linger_spin = _spin(60000)
        self.linger_max_spin = _spin(120000)
        for c, (text, spin) in enumerate((("Listen per profile", self.dwell_spin),
                                           ("Stay after packet", self.linger_spin),
                                           ("Stay after partial", self.partial_linger_spin),
                                           ("Max stay", self.linger_max_spin))):
            timing.addWidget(label(text, mono_style(10.5, DIM)), 0, c)
            timing.addWidget(spin, 1, c)
            timing.setColumnStretch(c, 1)
            spin.valueChanged.connect(lambda _=None: (self._update_estimate(), self.changed.emit()))
        hop.addLayout(timing)
        self.body.addWidget(self.hop_widget)

        # ---- several radios
        self.multi_toggle = QToolButton()
        self.multi_toggle.setCursor(Qt.PointingHandCursor)
        self.multi_toggle.setStyleSheet(mono_style(11, DIM, "background: transparent; border: none; padding: 0;"))
        self.multi_toggle.setText("▸  With several radios connected, the hop list is split between them")
        self.multi_toggle.clicked.connect(self._toggle_multi)
        self.body.addWidget(self.multi_toggle)
        self.multi_note = hint(
            "Several radios (connect to “All LoRa sniffers”): the profiles are shared out "
            "between them. Hop: the hop list is split across the radios, and a radio left with "
            "one profile stays on it. Fixed: the first radio takes the fixed profile, each other "
            "radio the next profile in the hop list.")
        self.multi_note.setContentsMargins(16, 0, 0, 0)
        self.multi_note.setVisible(False)
        self.body.addWidget(self.multi_note)

        self.set_values(config)

    # ---- table state --------------------------------------------------------------

    def is_checked(self, row):
        return self._checked[row]

    def set_checked(self, row, on, emit=True):
        self._checked[row] = on
        self._update_estimate()
        if emit:
            self.changed.emit()

    def _checked_keys(self):
        return [p.key for p, on in zip(self.profiles, self._checked) if on]

    def _dwell(self, profile):
        return self.dwell_spin.value() or profile.auto_dwell_ms()

    def catch_of(self, row):
        return self._catch.get(row, 0.0)

    def _update_estimate(self):
        chosen = [(row, p) for row, p in enumerate(self.profiles) if self._checked[row]]
        cycle = sum(self._dwell(p) + RETUNE_MS for _, p in chosen)
        self._catch = {row: catch_rate(p, self._dwell(p), cycle) for row, p in chosen}
        for row, p in enumerate(self.profiles):
            self.table.item(row, COL_DWELL).setText(f"{self._dwell(p)} ms")
            self.table.item(row, COL_CATCH).setText(f"{self._catch[row]:.0%}" if row in self._catch else "—")
        self.cycle_label.setText(f"≈ {cycle} ms" if chosen else "tick at least one profile")
        self.cycle_bar.set_segments([(p, self._dwell(p)) for _, p in chosen])
        self.table.viewport().update()

    def _on_mode(self, _key=None):
        hop = self.mode.value() == "hop"
        self.fixed_widget.setVisible(not hop)
        self.hop_widget.setVisible(hop)
        self.changed.emit()

    def _toggle_multi(self):
        show = not self.multi_note.isVisible()
        self.multi_note.setVisible(show)
        self.multi_toggle.setText(("▾" if show else "▸") + self.multi_toggle.text()[1:])

    # ---- values -------------------------------------------------------------------

    def set_values(self, config):
        self.blockSignals(True)
        self.mode.set_value(config.get("sniffer_mode", "hop"))
        idx = self.fixed_combo.findData(config.get("sniffer_fixed_profile", DEFAULT_FIXED_PROFILE))
        self.fixed_combo.setCurrentIndex(max(0, idx))
        hop_keys = set(config.get("sniffer_hop_profiles") or DEFAULT_HOP_PROFILES)
        self._checked = [p.key in hop_keys for p in self.profiles]
        self.dwell_spin.setValue(int(config.get("sniffer_dwell_ms", 0)))
        self.linger_spin.setValue(int(config.get("sniffer_linger_ms", 3000)))
        self.partial_linger_spin.setValue(int(config.get("sniffer_partial_linger_ms", 2000)))
        self.linger_max_spin.setValue(int(config.get("sniffer_linger_max_ms", 12000)))
        self._update_estimate()
        self._on_mode()
        self.blockSignals(False)

    def values(self):
        return {
            "sniffer_mode": self.mode.value(),
            "sniffer_fixed_profile": self.fixed_combo.currentData(),
            "sniffer_hop_profiles": self._checked_keys() or list(DEFAULT_HOP_PROFILES),
            "sniffer_dwell_ms": self.dwell_spin.value(),
            "sniffer_linger_ms": self.linger_spin.value(),
            "sniffer_partial_linger_ms": self.partial_linger_spin.value(),
            "sniffer_linger_max_ms": self.linger_max_spin.value(),
        }

    def summary(self):
        if self.mode.value() == "fixed":
            key = self.fixed_combo.currentData()
            name = next((p.name for p in self.profiles if p.key == key), key)
            return f"Fixed · {name}"
        n = len(self._checked_keys())
        return f"Hop · {n} profile{'' if n == 1 else 's'}"
