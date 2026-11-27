"""PACKET inspector (redesign 1a): everything known about the message row
selected in the Messages panel -- identity, signal margin over the noise
floor, the repeater path, every relayed copy heard, decoded fields and
the raw bytes.

Fed a message dict from core/messages.py (see that module for the keys);
rebuilt from scratch on each show_message(), which only happens on a
selection change or when the selected row folds in another copy.
"""

import json

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.messages import MESH_LABELS, channel_text, from_text
from ..theme import (
    ACCENT,
    ACCENT_DIM,
    ACCENT_LIGHT,
    DIM,
    LINE,
    LINE_SOFT,
    LIVE_PILL_BG,
    LOG_PATH,
    LOG_WARN,
    MESH_MESHCORE,
    MESH_MESHTASTIC,
    PANEL,
    TXT,
    pick_mono_font,
)
from .common import make_button
from .stat_tile import StatTile

WIDTH = 340
OVERLAY_WIDTH = 300
SCALE_LO, SCALE_HI = -110.0, -30.0
MESH_COLORS = {"meshcore": MESH_MESHCORE, "meshtastic": MESH_MESHTASTIC}
MESH_SHORT = {"meshcore": "MC", "meshtastic": "MT"}


def _frac(rssi):
    return min(1.0, max(0.0, (rssi - SCALE_LO) / (SCALE_HI - SCALE_LO)))


def _lbl(text, color=TXT, px=11, weight=None, spacing=None, selectable=False):
    mono = pick_mono_font()
    lbl = QLabel(text)
    style = f"font-family: '{mono}'; font-size: {px}px; color: {color}; background: transparent;"
    if weight:
        style += f" font-weight: {weight};"
    if spacing:
        style += f" letter-spacing: {spacing};"
    lbl.setStyleSheet(style)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lbl


def mesh_badge(mesh):
    color = MESH_COLORS.get(mesh, TXT)
    badge = _lbl(MESH_SHORT.get(mesh, "?"), color, 9, weight=700)
    badge.setStyleSheet(badge.styleSheet() + f" border: 1px solid {color}; border-radius: 3px; padding: 0 4px;")
    badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return badge


class FlowLayout(QLayout):
    """Left-to-right, wrapping (Qt's classic flow layout example)."""

    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _place(self, rect, dry):
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h:
                x, y = rect.x(), y + line_h + self._spacing
                line_h = 0
            if not dry:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class MarginBar(QWidget):
    """-110..-30 dBm track; gradient fill from the noise floor up to the
    packet's RSSI, with a tick at the floor."""

    def __init__(self, height=6, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.rssi = None
        self.floor = None

    def set_values(self, rssi, floor):
        self.rssi, self.floor = rssi, floor
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(LINE_SOFT))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        if self.rssi is None:
            return
        start = _frac(self.floor) * w if self.floor is not None else 0
        end = _frac(self.rssi) * w
        if end > start:
            grad = QLinearGradient(start, 0, end, 0)
            grad.setColorAt(0, QColor(ACCENT_DIM))
            grad.setColorAt(1, QColor(ACCENT))
            p.setBrush(grad)
            p.drawRoundedRect(QRectF(start, 0, end - start, h), h / 2, h / 2)
        if self.floor is not None:
            p.fillRect(QRectF(start - 0.5, -1, 1.5, h + 2), QColor(LOG_WARN))


class MiniBar(QWidget):
    """A plain 4 px RSSI bar for the Copies heard rows."""

    def __init__(self, rssi, parent=None):
        super().__init__(parent)
        self.setFixedHeight(4)
        self.rssi = rssi

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(LINE_SOFT))
        p.drawRoundedRect(QRectF(0, 0, self.width(), 4), 2, 2)
        if self.rssi is not None:
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(QRectF(0, 0, _frac(self.rssi) * self.width(), 4), 2, 2)


def hex_dump_html(raw_hex, per_line=16):
    data = bytes.fromhex(raw_hex) if raw_hex else b""
    lines = []
    for off in range(0, len(data), per_line):
        chunk = " ".join(f"{b:02x}" for b in data[off:off + per_line])
        lines.append(f"<span style='color:{ACCENT_DIM}'>{off:04x}</span>&nbsp;&nbsp;"
                     f"<span style='color:{LOG_PATH}'>{chunk}</span>")
    return "<br>".join(lines)


class OverlayShadow(QWidget):
    """Soft shadow painted behind the floating inspector (a sibling widget:
    QGraphicsDropShadowEffect on a scrolling panel repaints stale content
    and is slow). Approximates `0 12px 40px rgba(0,0,0,.7)`."""

    BLUR = 24
    OFFSET_Y = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def follow(self, rect):
        b = self.BLUR
        self.setGeometry(rect.adjusted(-b, -b + self.OFFSET_Y, b, b + self.OFFSET_Y))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        b = self.BLUR
        inner = QRectF(self.rect()).adjusted(b, b, -b, -b)
        steps = 12
        for k in range(steps):
            grow = b * (1 - k / steps)
            p.setBrush(QColor(0, 0, 0, int(178 / steps * 1.4)))
            p.drawRoundedRect(inner.adjusted(-grow, -grow, grow, grow), 10 + grow, 10 + grow)


class PacketInspector(QFrame):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("packetInspector")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(WIDTH)
        self._mono = pick_mono_font()
        self._message = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 0, 0, 0)  # 1 px for the left border
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("inspectorHeader")
        header.setFixedHeight(40)
        hrow = QHBoxLayout(header)
        hrow.setContentsMargins(14, 0, 8, 0)
        hrow.setSpacing(8)
        hrow.addWidget(_lbl("PACKET", ACCENT, 11, weight=600, spacing="0.1em"))
        self.subtitle = _lbl("", DIM, 11)
        hrow.addWidget(self.subtitle)
        hrow.addStretch(1)
        self.heard = _lbl("", ACCENT_LIGHT, 11)
        self.heard.setStyleSheet(self.heard.styleSheet()
                                 + f" background: {LIVE_PILL_BG}; border: 1px solid {ACCENT_DIM};"
                                 f" border-radius: 9px; padding: 1px 8px;")
        hrow.addWidget(self.heard)
        close = QToolButton()
        close.setText("×")
        close.setProperty("class", "Close")
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("Close (Esc)")
        close.clicked.connect(self.closed)
        hrow.addWidget(close)
        outer.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, stretch=1)
        self.body = None
        self._outer = outer

        # Overlay mode (small window): decoded fields + raw hex sit behind
        # "Raw hex", and "Copy JSON" copies the whole message.
        self._overlay = False
        self._details_open = False
        self.footer = QFrame()
        self.footer.setObjectName("inspectorFooter")
        frow = QHBoxLayout(self.footer)
        frow.setContentsMargins(10, 8, 10, 10)
        frow.setSpacing(8)
        self.raw_button = make_button("Raw hex", variant="secondary", height=28)
        self.raw_button.setCheckable(True)
        self.raw_button.toggled.connect(self._toggle_details)
        json_button = make_button("Copy JSON", variant="secondary", height=28)
        json_button.clicked.connect(self._copy_json)
        for btn in (self.raw_button, json_button):
            btn.setFixedHeight(28)
            btn.setStyleSheet("font-size: 11px;")
            frow.addWidget(btn, stretch=1)
        self.footer.setVisible(False)
        outer.addWidget(self.footer)

    def set_overlay(self, overlay):
        """Floating 300 px panel over the message table (small window)
        instead of a docked 340 px column."""
        self._overlay = overlay
        self.setFixedWidth(OVERLAY_WIDTH if overlay else WIDTH)
        self.footer.setVisible(overlay)
        if overlay:
            self._outer.setContentsMargins(1, 1, 1, 1)
            self.setStyleSheet(f"#packetInspector {{ border: 1px solid {LINE}; border-radius: 10px; }}"
                               f" #inspectorHeader {{ border-top-left-radius: 10px;"
                               f" border-top-right-radius: 10px; }}"
                               f" #inspectorFooter {{ background: {PANEL}; border-bottom-left-radius: 10px;"
                               f" border-bottom-right-radius: 10px; }}")
        else:
            self._outer.setContentsMargins(1, 0, 0, 0)
            self.setStyleSheet("")
        if self._message is not None:
            self.refresh(self._message)

    def _toggle_details(self, on):
        self._details_open = on
        if self._message is not None:
            self.refresh(self._message)

    def _copy_json(self):
        if self._message is not None:
            QGuiApplication.clipboard().setText(json.dumps(self._message, indent=2, default=str))

    # ---- content ----------------------------------------------------------

    def show_message(self, m, floor=None):
        self._message = m
        self._floor = floor
        when = m.get("time", "")
        kind = m.get("ptype") or m.get("kind", "").upper()
        self.subtitle.setText(f"{kind} · {when}" if kind else when)
        heard = m.get("heard", 1)
        self.heard.setText(f"heard ×{heard}")
        self.heard.setVisible(heard > 1)

        body = QWidget()
        body.setObjectName("inspectorBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(16)
        lay.addLayout(self._identity(m))
        if m.get("rssi") is not None or m.get("snr") is not None:
            lay.addLayout(self._signal(m, floor))
        if m.get("path_hops") or m.get("route"):
            lay.addLayout(self._path(m))
        if len(m.get("copies") or []) > 0:
            lay.addLayout(self._copies(m))
        if not self._overlay or self._details_open:
            lay.addLayout(self._decoded(m))
            if m.get("raw_hex"):
                lay.addLayout(self._raw(m))
        lay.addStretch(1)
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.scroll.setWidget(body)
        self.body = body

    def refresh(self, m):
        """Same row, new data (another copy heard) -- keep the scroll spot."""
        pos = self.scroll.verticalScrollBar().value()
        self.show_message(m, self._floor)
        self.scroll.verticalScrollBar().setValue(pos)

    def current(self):
        return self._message

    def _section(self, title, right=None):
        col = QVBoxLayout()
        col.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(_lbl(title, DIM, 10, spacing="0.06em"))
        head.addStretch(1)
        if right is not None:
            head.addWidget(right)
        col.addLayout(head)
        return col

    def _identity(self, m):
        col = QVBoxLayout()
        col.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(mesh_badge(m.get("mesh")))
        chan = channel_text(m) or MESH_LABELS.get(m.get("mesh"), "")
        top.addWidget(_lbl(chan, TXT, 12))
        if m.get("decrypted") and m.get("kind") in ("text", "dm"):
            top.addWidget(_lbl("decrypted", DIM, 11))
        top.addStretch(1)
        col.addLayout(top)
        sender = from_text(m) or "—"
        col.addWidget(_lbl(sender, ACCENT, 16, weight=500, selectable=True))
        box = _lbl(m.get("text") or "", TXT if m.get("kind") in ("text", "dm") else DIM, 13, selectable=True)
        box.setProperty("class", "Box")
        box.setWordWrap(True)
        box.setStyleSheet(box.styleSheet().replace("background: transparent;", "")
                          + " padding: 10px 12px; line-height: 150%;")
        col.addWidget(box)
        return col

    def _signal(self, m, floor):
        col = self._section("SIGNAL")
        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        for caption, value, unit in (("RSSI", m.get("rssi"), "dBm"), ("SNR", m.get("snr"), "dB")):
            tile = StatTile(caption)
            if value is None:
                tile.set_value("—")
            else:
                text = f"{value:.1f}" if caption == "RSSI" else f"{value:.2f}"
                tile.value_label.setTextFormat(Qt.RichText)
                tile.value_label.setText(
                    f"<span style='font-size:17px; color:{ACCENT}'>{text}</span>"
                    f"<span style='font-size:11px; color:{DIM}'>&nbsp;{unit}</span>")
            tiles.addWidget(tile)
        col.addLayout(tiles)
        rssi = m.get("rssi")
        if rssi is not None:
            bar = MarginBar()
            bar.set_values(rssi, floor)
            col.addWidget(bar)
            cap = QHBoxLayout()
            cap.addWidget(_lbl(f"floor {floor:.0f}" if floor is not None else f"{SCALE_LO:.0f}", DIM, 10))
            cap.addStretch(1)
            if floor is not None:
                cap.addWidget(_lbl(f"{rssi - floor:.0f} dB above floor", ACCENT_LIGHT, 10))
                cap.addStretch(1)
            cap.addWidget(_lbl(f"{SCALE_HI:.0f}", DIM, 10))
            col.addLayout(cap)
        return col

    def _path(self, m):
        hops = m.get("path_hops") or []
        count = _lbl(f"{len(hops)} hop" + ("" if len(hops) == 1 else "s"), DIM, 10)
        col = self._section(f"PATH · {m.get('route') or '?'}", count)
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        flow = FlowLayout(holder, spacing=4)
        for i, h in enumerate(hops):
            chip = _lbl(h, TXT, 11)
            chip.setProperty("class", "PathChip")
            chip.setProperty("last", "true" if i == len(hops) - 1 else "false")
            chip.setStyleSheet(chip.styleSheet().replace("background: transparent;", ""))
            flow.addWidget(chip)
        flow.addWidget(_lbl("→ you", DIM, 11))
        col.addWidget(holder)
        return col

    def _copies(self, m):
        col = self._section("COPIES HEARD")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        grid.setColumnMinimumWidth(0, 92)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, 52)
        grid.setColumnMinimumWidth(3, 44)
        for r, c in enumerate(m.get("copies") or []):
            grid.addWidget(_lbl(c.get("t", ""), DIM, 11), r, 0)
            grid.addWidget(MiniBar(c.get("rssi")), r, 1, Qt.AlignVCenter)
            rssi = c.get("rssi")
            grid.addWidget(_lbl(f"{rssi:.1f}" if rssi is not None else "—", TXT, 11), r, 2, Qt.AlignRight)
            hops = c.get("path_len")
            grid.addWidget(_lbl(f"{hops} h" if hops is not None else "", DIM, 11), r, 3, Qt.AlignRight)
        col.addLayout(grid)
        return col

    def _decoded(self, m):
        col = self._section("DECODED")
        rows = []
        if m.get("ptype"):
            rows.append(("type", m["ptype"]))
        if m.get("route"):
            rows.append(("route", m["route"]))
        if m.get("chan_hash") is not None:
            name = channel_text(m)
            chan = f"{m['chan_hash']} → {name}" if m.get("decrypted") and name else str(m["chan_hash"])
            rows.append(("chan_hash", chan))
        if m.get("hops") is not None:
            rows.append(("path_len", str(m["hops"])))
        if m.get("payload_len") is not None:
            note = " · AES-128 ok" if m.get("decrypted") and m.get("mesh") == "meshcore" and m.get("chan_hash") else ""
            rows.append(("payload", f"{m['payload_len']} B{note}"))
        if m.get("to"):
            rows.append(("to", m["to"]))
        if m.get("profile_label") or m.get("profile"):
            rows.append(("profile", m.get("profile_label") or m["profile"]))
        rows.append(("source", m.get("source") or "?"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setColumnMinimumWidth(0, 84)
        grid.setColumnStretch(1, 1)
        for r, (k, v) in enumerate(rows):
            grid.addWidget(_lbl(k, DIM, 11), r, 0, Qt.AlignTop)
            val = _lbl(v, TXT, 11, selectable=True)
            val.setToolTip(v)
            val.setMinimumWidth(1)
            val.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            grid.addWidget(val, r, 1)
        col.addLayout(grid)
        return col

    def _raw(self, m):
        copy = QToolButton()
        copy.setText("Copy hex")
        copy.setProperty("class", "Link")
        copy.setCursor(Qt.PointingHandCursor)
        copy.setStyleSheet(f"font-family: '{self._mono}'; font-size: 10px;")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(m.get("raw_hex", "")))
        n = len(m.get("raw_hex", "")) // 2
        col = self._section(f"RAW · {n} B", copy)
        dump = QLabel(hex_dump_html(m.get("raw_hex", ""), 8 if self._overlay else 16))
        dump.setTextFormat(Qt.RichText)
        dump.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dump.setProperty("class", "Box")
        dump.setStyleSheet(f"font-family: '{self._mono}'; font-size: 10px; padding: 8px 10px;"
                           f" border-radius: 6px; line-height: 160%;")
        col.addWidget(dump)
        return col
