"""Multi-radio band overview (redesign 1d): an 80 px strip spanning the
902-928 MHz band with a marker and a "radio · profile · frequency" chip
for every profile assigned to a radio, so it's clear at a glance which
radio covers which part of the band. Shown above the RF band's views
only while two or more sniffer radios are connected.
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..theme import DIM, LINE, LINE_SOFT, TERMINAL_BG, TXT, pick_mono_font
from .top_bar import family_color, short_profile

BAND_LO, BAND_HI = 902.0, 928.0  # MHz
TICK_MHZ = 3


class BandStrip(QWidget):
    HEIGHT = 80

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self._markers = []  # (freq MHz, radio number, profile name, family)
        self._mono = pick_mono_font()

    def set_assignments(self, profiles, radios):
        """profiles: the sniffer_profiles payload (name, freq, family,
        radio label); radios: the sniffer_radios payload, for numbering."""
        numbers = {r["label"]: n for n, r in enumerate(radios, start=1)}
        self._markers = sorted(
            (float(p["freq"]), numbers.get(p.get("radio"), 0), p["name"], p.get("family", ""))
            for p in profiles)
        self.update()

    def _range(self):
        freqs = [f for f, *_ in self._markers]
        lo = min([BAND_LO] + [f - 1 for f in freqs])
        hi = max([BAND_HI] + [f + 1 for f in freqs])
        return lo, hi

    def paintEvent(self, _event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(TERMINAL_BG))
        lo, hi = self._range()
        pad = 10
        span_w = w - 2 * pad

        def x_of(mhz):
            return pad + (mhz - lo) / (hi - lo) * span_w

        font = QFont(self._mono)
        font.setPixelSize(10)
        p.setFont(font)
        fm = p.fontMetrics()

        p.setPen(QColor(DIM))
        p.drawText(QRectF(pad, 2, w, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   f"BAND  {lo:g} – {hi:g} MHz · profiles assigned to radios")

        # Axis along the bottom, a label every TICK_MHZ.
        axis_y = h - 14
        p.setPen(QColor(LINE))
        p.drawLine(int(pad), axis_y, int(w - pad), axis_y)
        f = int(lo) + (-int(lo)) % TICK_MHZ
        while f <= hi:
            x = x_of(f)
            p.setPen(QColor(LINE_SOFT))
            p.drawLine(int(x), 18, int(x), axis_y)
            p.setPen(QColor(DIM))
            p.drawText(QRectF(x - 20, axis_y + 1, 40, 12), Qt.AlignCenter, f"{f}")
            f += TICK_MHZ

        # Markers + chips; chips stack into rows so neighbours don't collide.
        rows_end = []  # right edge of the last chip per row
        chip_h = 16
        top = 20
        p.setRenderHint(QPainter.Antialiasing)
        for freq, number, name, family in self._markers:
            color = QColor(family_color(family))
            x = x_of(freq)
            p.setPen(QPen(color, 1))
            p.drawLine(int(x) + 0.5, top, int(x) + 0.5, axis_y)
            text = f"{number} · {short_profile(name)} · {freq:.3f}" if number else f"{short_profile(name)} · {freq:.3f}"
            tw = fm.horizontalAdvance(text) + 12
            cx = min(max(pad, x - tw / 2), w - pad - tw)
            row = next((i for i, end in enumerate(rows_end) if cx > end + 4), None)
            if row is None:
                if len(rows_end) >= 3:
                    continue  # no room; the marker line still shows
                rows_end.append(0)
                row = len(rows_end) - 1
            rows_end[row] = cx + tw
            box = QRectF(cx, top + row * (chip_h + 2), tw, chip_h)
            p.setBrush(QColor(TERMINAL_BG))
            p.setPen(QPen(color, 1))
            p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
            p.setPen(QColor(TXT))
            p.drawText(box, Qt.AlignCenter, text)
