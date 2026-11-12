"""WATERFALL pane: live spectrum + scrolling waterfall from the HAT's
RTL-SDR, centred on the mesh frequency, with the LoRa channel's bandwidth
highlighted so you can see packets land on it.

Rows arrive (numpy float32 dBFS arrays) from WaterfallWorker via the
Bridge; this widget only colour-maps and paints them.
"""

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..theme import ACCENT, DIM, LINE, TERMINAL_BG, TERMINAL_DIVIDER, pick_mono_font
from .common import section_label

HISTORY_ROWS = 400
SPECTRUM_HEIGHT = 70

# label -> sample rate (= displayed span)
SPANS = {"2.4 MHz": 2_400_000, "1.0 MHz": 1_024_000}
GAINS = {"Auto": None, "20 dB": 20.0, "30 dB": 30.0, "40 dB": 40.0, "49 dB": 49.6}


def _build_lut():
    """Google's Turbo colormap (polynomial fit): dark blue noise floor ->
    cyan -> green -> yellow -> red for the strongest signals. Much easier
    to read at a glance (and in sunlight) than a single-hue ramp."""
    x = np.linspace(0, 1, 256)
    r = 0.13572138 + x * (4.61539260 + x * (-42.66032258 + x * (132.13108234 + x * (-152.94239396 + x * 59.28637943))))
    g = 0.09140261 + x * (2.19418839 + x * (4.84296658 + x * (-14.18503333 + x * (4.27729857 + x * 2.82956604))))
    b = 0.10667330 + x * (12.64194608 + x * (-60.58204836 + x * (110.36276771 + x * (-89.90310912 + x * 27.34824973))))
    rgb = (np.clip(np.stack([r, g, b], axis=1), 0, 1) * 255).astype(np.uint32)
    return (0xFF << 24) | (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]


_LUT = _build_lut()


class WaterfallCanvas(QWidget):
    def __init__(self, parent=None, font_px=10, spectrum_height=SPECTRUM_HEIGHT):
        super().__init__(parent)
        self.setMinimumHeight(160)
        # Bigger labels / a shorter spectrum strip for the 640x480 pocket UI.
        self.font_px = font_px
        self.axis_height = font_px + 8
        self.spectrum_height = spectrum_height
        self._mono = pick_mono_font()
        self._rows = None  # (HISTORY_ROWS, bins) uint8 colour indices, newest first
        self._filled = 0
        self._spectrum = None
        self._floor = None
        self.center_hz = 0
        self.span_hz = 1
        self.channel_bw_hz = 0

    def configure(self, center_hz, span_hz, channel_bw_hz):
        self.center_hz = center_hz
        self.span_hz = span_hz
        self.channel_bw_hz = channel_bw_hz
        self.clear()

    def clear(self):
        self._rows = None
        self._filled = 0
        self._spectrum = None
        self._floor = None
        self.update()

    def add_row(self, row):
        # Auto-level: track the noise floor (a low percentile) with an EMA
        # so a single burst doesn't pump the whole display.
        floor = float(np.percentile(row, 20))
        self._floor = floor if self._floor is None else 0.95 * self._floor + 0.05 * floor
        lo, hi = self._floor - 3, self._floor + 35
        idx = np.clip((row - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
        if self._rows is None or self._rows.shape[1] != len(row):
            self._rows = np.zeros((HISTORY_ROWS, len(row)), dtype=np.uint8)
        self._rows[1:] = self._rows[:-1]
        self._rows[0] = idx
        self._filled = min(self._filled + 1, HISTORY_ROWS)
        self._spectrum = (row, lo, hi)
        self.update()

    def _freq_to_x(self, hz, width):
        return (hz - (self.center_hz - self.span_hz / 2)) / self.span_hz * width

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(TERMINAL_BG))
        spec_top = self.axis_height
        wf_top = self.axis_height + self.spectrum_height
        wf_rect = QRectF(0, wf_top, w, h - wf_top)

        if self._rows is not None:
            img_data = np.ascontiguousarray(_LUT[self._rows])
            img = QImage(img_data.data, img_data.shape[1], img_data.shape[0], img_data.shape[1] * 4, QImage.Format_ARGB32)
            # One image row per pixel; rows not yet received stay
            # background rather than showing the colormap's lowest colour.
            rows_shown = min(self._filled, max(1, int(wf_rect.height())))
            dest = QRectF(0, wf_top, w, rows_shown)
            p.drawImage(dest, img, QRectF(0, 0, img_data.shape[1], rows_shown))

        if self._spectrum is not None:
            row, lo, hi = self._spectrum
            n = len(row)
            ys = spec_top + self.spectrum_height - np.clip((row - lo) / (hi - lo), 0, 1) * (self.spectrum_height - 4)
            xs = np.arange(n) * (w / max(1, n - 1))
            path = QPainterPath()
            path.moveTo(xs[0], ys[0])
            for x, y in zip(xs[1:], ys[1:]):
                path.lineTo(x, y)
            p.setPen(QPen(QColor("#ffe14d"), 2))
            p.drawPath(path)

        # LoRa channel band
        if self.channel_bw_hz:
            x0 = self._freq_to_x(self.center_hz - self.channel_bw_hz / 2, w)
            x1 = self._freq_to_x(self.center_hz + self.channel_bw_hz / 2, w)
            band = QColor("#ffffff")
            band.setAlpha(40)
            p.fillRect(QRectF(x0, spec_top, x1 - x0, self.spectrum_height), band)
            pen = QPen(QColor("#ffffff"), 2, Qt.DashLine)
            p.setPen(pen)
            p.drawLine(int(x0), spec_top, int(x0), h)
            p.drawLine(int(x1), spec_top, int(x1), h)

        # Frequency axis
        p.fillRect(QRectF(0, 0, w, self.axis_height), QColor(TERMINAL_BG))
        p.setPen(QColor(LINE))
        p.drawLine(0, wf_top, w, wf_top)
        font = p.font()
        font.setFamily(self._mono)
        font.setPixelSize(self.font_px)
        p.setFont(font)
        # Pick a tick spacing that leaves room for each label.
        label_px = self.font_px * 6
        step = next(
            (s for s in (100_000, 200_000, 250_000, 500_000, 1_000_000) if w * s / self.span_hz >= label_px),
            1_000_000,
        )
        start = self.center_hz - self.span_hz / 2
        f = np.ceil(start / step) * step
        while f <= start + self.span_hz:
            x = self._freq_to_x(f, w)
            p.setPen(QColor("#9aa59a"))
            p.drawLine(int(x), self.axis_height - 4, int(x), self.axis_height)
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(x - 50, 0, 100, self.axis_height - 4), Qt.AlignCenter, f"{f / 1e6:.3f}")
            f += step

        if self._rows is None:
            p.setPen(QColor("#ffffff"))
            p.drawText(wf_rect, Qt.AlignCenter, "waterfall idle")
        p.end()


class WaterfallView(QWidget):
    sdr_settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("terminalPanel")
        mono = pick_mono_font()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("terminalHeader")
        header.setFixedHeight(44)
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 0, 16, 0)
        title = section_label("WATERFALL")
        title.setStyleSheet(title.styleSheet() + f" color: {ACCENT};")
        row.addWidget(title)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"background-color: {TERMINAL_DIVIDER}; max-height: 1px; border: none;")
        row.addWidget(line, stretch=1)

        self.status_label = QLabel("RTL-SDR idle")
        self.status_label.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; color: {DIM};")
        row.addWidget(self.status_label)

        for label_text, attr, options in (("span", "span_combo", SPANS), ("gain", "gain_combo", GAINS)):
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
            row.addWidget(lbl)
            combo = QComboBox()
            combo.addItems(options)
            combo.currentTextChanged.connect(self.sdr_settings_changed)
            setattr(self, attr, combo)
            row.addWidget(combo)
        layout.addWidget(header)

        self.canvas = WaterfallCanvas()
        layout.addWidget(self.canvas, stretch=1)

    def set_sdr_values(self, sample_rate, gain_db):
        for combo, mapping, value in ((self.span_combo, SPANS, sample_rate), (self.gain_combo, GAINS, gain_db)):
            combo.blockSignals(True)
            label = next((k for k, v in mapping.items() if v == value), next(iter(mapping)))
            combo.setCurrentText(label)
            combo.blockSignals(False)

    def sample_rate(self):
        return SPANS[self.span_combo.currentText()]

    def gain_db(self):
        return GAINS[self.gain_combo.currentText()]

    def set_status(self, text, active=False):
        self.status_label.setText(text)
        mono = pick_mono_font()
        self.status_label.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; color: {ACCENT if active else DIM};")
