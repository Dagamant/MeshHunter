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

from ..theme import ACCENT, DIM, INK, LINE, LINE_SOFT, PANEL, pick_mono_font
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
        p.fillRect(self.rect(), QColor(INK))
        p.setRenderHint(QPainter.Antialiasing, True)
        small = QFont(self._mono)
        small.setPixelSize(10)
        p.setFont(small)

        now = time.monotonic()
        cutoff = now - _WINDOW_S
        while self._dots and self._dots[0][0] < cutoff:
            self._dots.popleft()

        floor_dbm = [v for s in self._series.values() for _, v in s]
        dot_dbm = [d for _, d, _ in self._dots]
        if not floor_dbm and not dot_dbm:
            p.setPen(QColor(DIM))
            p.drawText(self.rect(), Qt.AlignCenter, "noise\nfloor")
            return

        w, h = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 30, 16, 16, 14
        plot = QRectF(pad_l, pad_t, w - pad_l - pad_r, h - pad_t - pad_b)

        vals = floor_dbm + dot_dbm
        lo = min(vals)
        hi = max(vals)
        lo, hi = min(lo, hi - 10), max(hi, lo + 10)  # at least a 10 dB span
        lo = max(_FLOOR_MIN_DB, lo - 3)
        hi = min(_FLOOR_MAX_DB, hi + 3)
        if hi <= lo:
            hi = lo + 10

        def xy(t, dbm):
            fx = 1.0 - min(1.0, (now - t) / _WINDOW_S)
            fy = (dbm - lo) / (hi - lo)
            return QPointF(plot.left() + fx * plot.width(),
                           plot.bottom() - fy * plot.height())

        # Grid + dBm labels (top, middle, bottom).
        p.setPen(QColor(LINE_SOFT))
        for frac in (0.0, 0.5, 1.0):
            y = plot.bottom() - frac * plot.height()
            p.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))
            p.setPen(QColor(DIM))
            p.drawText(QRectF(0, y - 7, pad_l - 3, 14), Qt.AlignRight | Qt.AlignVCenter,
                       f"{lo + frac * (hi - lo):.0f}")
            p.setPen(QColor(LINE_SOFT))

        p.setPen(QColor(DIM))
        p.drawText(QRectF(0, 0, w, pad_t), Qt.AlignHCenter | Qt.AlignVCenter, "SIGNAL · NOISE dBm")

        for label in self._order:
            series = self._series.get(label) or ()
            if not series:
                continue
            p.setPen(QPen(self._color(label), 1.4))
            prev = None
            for t, dbm in series:
                cur = xy(t, dbm)
                if prev is not None:
                    p.drawLine(prev, cur)
                prev = cur
            if prev is not None:
                p.fillRect(QRectF(prev.x() - 1.5, prev.y() - 1.5, 3, 3), self._color(label))

        # Received-packet dots, on top of the noise line: each sits at its own
        # RSSI, so the gap up from the line is how far it rose above the floor.
        p.setPen(Qt.NoPen)
        for t, dbm, kind in self._dots:
            if t < cutoff:
                continue
            c = xy(t, dbm)
            if plot.left() - 1 <= c.x() <= plot.right() + 1:
                p.setBrush(_DOT_COLORS.get(kind, _DOT_COLORS["ok"]))
                p.drawEllipse(c, 1.8, 1.8)
        p.setBrush(Qt.NoBrush)


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
