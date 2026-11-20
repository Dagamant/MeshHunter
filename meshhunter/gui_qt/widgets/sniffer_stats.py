"""Live LoRa-sniffer telemetry UI, fed by the worker's sniffer_stat event
(see core/sniffer_worker.py._on_stat, ~every 5 s):

  NoiseGraph       a noise-floor-over-time line chart, one line per radio,
                   shown beside the waterfall.
  SnifferStatsBar  a thin strip of value tiles (noise, RX ok/bad, preambles,
                   hops) that sits between the terminal and the decoder --
                   the content that used to scroll past as SNIFF_STAT lines.
"""

import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..theme import ACCENT, DIM, LINE, LINE_SOFT, PANEL, TERMINAL_BG, TXT, pick_mono_font
from .common import section_label
from .stat_tile import StatTile

# A small fixed palette so each radio keeps one line color.
_LINE_COLORS = [ACCENT, "#5ab1ff", "#ff9d5a", "#c77dff", "#ffe066"]
# Received-packet dot colors, matching the waterfall's packet markers.
_DOT_COLORS = {"ok": QColor("#ffffff"), "crc": QColor("#c9b06a"), "partial": QColor("#ff7878")}
# The dBm axis is clamped to this band (noise floor near the bottom, strong
# signals near the top) and auto-ranges within it to fit what's on screen.
_FLOOR_MIN_DB = -135
_FLOOR_MAX_DB = -20
_WINDOW_S = 600  # keep ~10 minutes of history
_MAX_DOTS = 2000


class NoiseGraph(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumWidth(240)
        self._mono = pick_mono_font()
        self._series = {}  # radio label -> deque[(t, dbm)]
        self._order = []   # radio labels in first-seen order, for stable colors
        self._dots = deque(maxlen=_MAX_DOTS)  # (t, dbm, kind) per received packet

    def add_event(self, payload):
        """A received packet (from sniffer_event): plot it as a dot at its
        RSSI, so you can see how far above the noise floor it landed."""
        rssi = payload.get("rssi")
        if rssi is None:
            return
        self._dots.append((time.monotonic(), float(rssi), payload.get("kind", "ok")))
        self.update()

    def add_stat(self, payload):
        noise = payload.get("noise")
        if noise is None:
            return
        label = payload.get("radio") or ""
        if label not in self._series:
            self._series[label] = deque()
            self._order.append(label)
        now = time.monotonic()
        s = self._series[label]
        s.append((now, float(noise)))
        cutoff = now - _WINDOW_S
        while s and s[0][0] < cutoff:
            s.popleft()
        self.update()

    def clear(self):
        self._series.clear()
        self._order.clear()
        self._dots.clear()
        self.update()

    def _color(self, label):
        return QColor(_LINE_COLORS[self._order.index(label) % len(_LINE_COLORS)])

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(TERMINAL_BG))
        p.fillRect(QRectF(0, 0, 1, h), QColor(LINE_SOFT))
        small = QFont(self._mono)
        small.setPixelSize(9)
        head = QFont(self._mono)
        head.setPixelSize(11)

        # Header: title + legend.
        p.setFont(head)
        p.setPen(QColor(ACCENT))
        head.setWeight(QFont.DemiBold)
        p.setFont(head)
        p.drawText(QRectF(10, 0, w, 18), Qt.AlignLeft | Qt.AlignVCenter, "SIGNAL / NOISE")
        p.setFont(small)
        fm = p.fontMetrics()
        x = w - 8 - fm.horizontalAdvance("floor")
        p.setPen(QColor(DIM))
        p.drawText(QRectF(x, 0, 60, 18), Qt.AlignLeft | Qt.AlignVCenter, "floor")
        p.fillRect(QRectF(x - 16, 8, 12, 2), QColor(ACCENT))
        x -= 24 + fm.horizontalAdvance("packet")
        p.drawText(QRectF(x, 0, 60, 18), Qt.AlignLeft | Qt.AlignVCenter, "packet")
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(TXT))
        p.drawEllipse(QPointF(x - 7, 9), 2.5, 2.5)
        p.setBrush(Qt.NoBrush)

        now = time.monotonic()
        cutoff = now - _WINDOW_S
        while self._dots and self._dots[0][0] < cutoff:
            self._dots.popleft()

        pad_l, pad_r, pad_t, pad_b = 34, 10, 24, 16
        plot = QRectF(pad_l, pad_t, w - pad_l - pad_r, h - pad_t - pad_b)

        floor_dbm = [v for s in self._series.values() for _, v in s]
        dot_dbm = [d for _, d, _ in self._dots]
        vals = floor_dbm + dot_dbm
        # Fixed -110..-30 dBm, widened only if something falls outside it.
        lo = min([-110.0] + [v - 3 for v in vals])
        hi = max([-30.0] + [v + 3 for v in vals])
        lo, hi = max(_FLOOR_MIN_DB, lo), min(_FLOOR_MAX_DB + 10, hi)

        def xy(t, dbm):
            fx = 1.0 - min(1.0, (now - t) / _WINDOW_S)
            fy = (dbm - lo) / (hi - lo)
            return QPointF(plot.left() + fx * plot.width(), plot.bottom() - fy * plot.height())

        # Y axis labels (top/mid/bottom) and a dashed midline; x axis labels.
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(QColor(DIM))
        for frac in (1.0, 0.5, 0.0):
            y = plot.bottom() - frac * plot.height()
            p.drawText(QRectF(0, y - 7, pad_l - 5, 14), Qt.AlignRight | Qt.AlignVCenter, f"{lo + frac * (hi - lo):.0f}")
        mid = plot.center().y()
        p.setPen(QPen(QColor(LINE_SOFT), 1, Qt.DashLine))
        p.drawLine(QPointF(plot.left(), mid), QPointF(plot.right(), mid))
        p.setPen(QColor(LINE_SOFT))
        p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        p.setPen(QColor(DIM))
        ax = QRectF(plot.left(), plot.bottom() + 2, plot.width(), pad_b - 2)
        p.drawText(ax, Qt.AlignLeft | Qt.AlignVCenter, "−10 min")
        p.drawText(ax, Qt.AlignHCenter | Qt.AlignVCenter, "−5")
        p.drawText(ax, Qt.AlignRight | Qt.AlignVCenter, "now")

        if not vals:
            p.drawText(plot, Qt.AlignCenter, "waiting for sniffer telemetry")
            return

        p.setRenderHint(QPainter.Antialiasing, True)
        for label in self._order:
            series = self._series.get(label) or ()
            if not series:
                continue
            p.setPen(QPen(self._color(label), 1.2))
            prev = None
            for t, dbm in series:
                cur = xy(t, dbm)
                if prev is not None:
                    p.drawLine(prev, cur)
                prev = cur

        # Received packets: small squares at their RSSI, so the gap up from
        # the floor line is how far each rose above the noise.
        p.setRenderHint(QPainter.Antialiasing, False)
        for t, dbm, kind in self._dots:
            if t < cutoff:
                continue
            c = xy(t, dbm)
            if plot.left() - 1 <= c.x() <= plot.right() + 1:
                color = QColor(TXT) if kind == "ok" else _DOT_COLORS.get(kind, _DOT_COLORS["ok"])
                p.fillRect(QRectF(c.x() - 1.2, c.y() - 1.2, 2.4, 2.4), color)


class SnifferStatsBar(QWidget):
    """A compact live readout of the latest telemetry, aggregated across
    radios (counters summed; noise shown as the quietest radio's floor)."""

    _TILES = [
        ("noise", "Noise floor"),
        ("ok", "RX packets"),
        ("bad", "RX errors"),
        ("pre", "Preambles"),
        ("hops", "Hops"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("snifferStatsBar")
        self._latest = {}  # radio label -> last stat payload
        mono = pick_mono_font()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 6, 16, 6)
        outer.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(section_label("SNIFFER STATS"))
        self.detail = QLabel("")
        self.detail.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        head.addWidget(self.detail, stretch=1)
        outer.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.tiles = {}
        for key, caption in self._TILES:
            tile = StatTile(caption)
            tile.set_value("—")
            self.tiles[key] = tile
            row.addWidget(tile)
        row.addStretch(1)
        outer.addLayout(row)

        self.setStyleSheet(
            f"#snifferStatsBar {{ background: {PANEL}; border-top: 1px solid {LINE};"
            f" border-bottom: 1px solid {LINE}; }}"
        )

    def add_stat(self, payload):
        self._latest[payload.get("radio") or ""] = payload
        multi = any(p.get("multi") for p in self._latest.values())

        ok = sum(p.get("ok") or 0 for p in self._latest.values())
        bad = sum(p.get("bad") or 0 for p in self._latest.values())
        pre = sum(p.get("pre") or 0 for p in self._latest.values())
        hops = sum(p.get("hops") or 0 for p in self._latest.values())
        noises = [p["noise"] for p in self._latest.values() if p.get("noise") is not None]

        self.tiles["noise"].set_value(f"{min(noises):.0f} dBm" if noises else "—")
        self.tiles["ok"].set_value(ok)
        self.tiles["bad"].set_value(bad)
        self.tiles["pre"].set_value(pre)
        self.tiles["hops"].set_value(hops)

        if multi and noises:
            self.detail.setText("  ·  ".join(
                f"{p['radio']}: {p['noise']:.0f} dBm" for p in self._latest.values()
                if p.get("noise") is not None
            ))
        else:
            self.detail.setText("RX errors = bad CRC + header errors + timeouts")

    def clear(self):
        self._latest.clear()
        for tile in self.tiles.values():
            tile.set_value("—")
        self.detail.setText("")
