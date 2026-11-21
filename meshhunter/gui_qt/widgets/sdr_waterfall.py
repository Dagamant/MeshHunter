"""WATERFALL pane: live spectrum + scrolling waterfall from the HAT's
RTL-SDR, centred on the mesh frequency, with the LoRa channel's bandwidth
highlighted so you can see packets land on it.

Rows arrive (numpy float32 dBFS arrays) from WaterfallWorker via the
Bridge; this widget only colour-maps and paints them.
"""

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QToolTip, QVBoxLayout, QWidget

from ...core.band_plan import KIND_COLORS, Band, assign_lanes, bands_at, bands_in

from ..theme import ACCENT, ACCENT_LIGHT, DIM, LINE, TERMINAL_BG, TERMINAL_DIVIDER, pick_mono_font
from .common import section_label

HISTORY_ROWS = 400
SPECTRUM_HEIGHT = 70
MAX_BAND_LANES = 3

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


def _build_green_lut():
    """The desktop redesign's colormap: black -> deep green -> logo green
    -> near white, matching the rest of the UI."""
    stops = [(0.00, (0, 0, 0)), (0.32, (2, 22, 9)), (0.55, (10, 80, 22)), (0.78, (57, 230, 20)),
             (1.00, (225, 255, 205))]
    x = np.linspace(0, 1, 256)
    pos = [t for t, _ in stops]
    rgb = np.stack([np.interp(x, pos, [c[i] for _, c in stops]) for i in range(3)], axis=1).astype(np.uint32)
    return (0xFF << 24) | (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]


GREEN_LUT = _build_green_lut()


class WaterfallCanvas(QWidget):
    def __init__(self, parent=None, font_px=10, spectrum_height=SPECTRUM_HEIGHT, row_px=1, axis_height=None,
                 lut=None, trace_color="#ffe14d", trace_fill=False, show_bands=False):
        super().__init__(parent)
        # Labelled strip of known allocations (core/band_plan.py) between
        # the frequency axis and the spectrum, plus a hover tooltip.
        self.show_bands = show_bands
        self._band_cache = (None, [])
        self._mesh_bands = []  # Band per sniffer profile, same order as self.channels
        if show_bands:
            self.setMouseTracking(True)
        self.lut = _LUT if lut is None else lut
        self.trace_color = trace_color
        self.trace_fill = trace_fill
        # Desktop redesign style (trace_fill) uses dim axis labels.
        self.axis_text_color = DIM if trace_fill else "#ffffff"
        # Pixels per waterfall row: >1 for slow feeds (e.g. a multi-segment
        # sweep) so the picture isn't a thin sliver.
        self.row_px = row_px
        self.setMinimumHeight(160)
        # Bigger labels / a shorter spectrum strip for the 640x480 pocket UI.
        self.font_px = font_px
        self.axis_height = axis_height or font_px + 8
        self.spectrum_height = spectrum_height
        self._mono = pick_mono_font()
        self._rows = None  # (HISTORY_ROWS, bins) uint8 colour indices, newest first
        self._filled = 0
        self._spectrum = None
        self._floor = None
        self.center_hz = 0
        self.span_hz = 1
        self.channel_bw_hz = 0
        # Optional several labelled channels (e.g. every sniffer profile)
        # instead of the single centred channel_bw_hz band.
        self.channels = []  # [(center_hz, bw_hz, label)]
        self.active = set()  # indices into channels being listened to now
        self.idle_text = "waterfall idle"

    def configure(self, center_hz, span_hz, channel_bw_hz):
        self.center_hz = center_hz
        self.span_hz = span_hz
        self.channel_bw_hz = channel_bw_hz
        self.clear()

    def set_channels(self, channels):
        self.channels = list(channels)
        self.active = set()
        self.update()

    def set_active(self, indices):
        indices = set(indices)
        if indices != self.active:
            self.active = indices
            self.update()

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

    def _x_to_freq(self, x, width):
        return self.center_hz - self.span_hz / 2 + x / max(1, width) * self.span_hz

    # ---- band plan --------------------------------------------------------------

    def _lane_height(self):
        return self.font_px + 5

    def set_mesh_bands(self, bands):
        """[(center_hz, bw_hz, label, family)]: the sniffer profiles, drawn
        as bars in their own lane(s) under the band plan."""
        self._mesh_bands = [Band((c - bw / 2) / 1e6, (c + bw / 2) / 1e6, label,
                                 f"{label} sniffer profile, {c / 1e6:.3f} MHz / BW {bw / 1e3:g} kHz",
                                 family if family in KIND_COLORS else "meshcore")
                            for c, bw, label, family in bands]
        self._band_cache = (None, [])
        self.update()

    def _band_lanes(self):
        """[(band, lane)] for the visible span (cached per tuning): known
        allocations on top (at most MAX_BAND_LANES), then the sniffer
        profiles in lane(s) of their own just above the spectrum."""
        if not self.show_bands or self.span_hz <= 1:
            return []
        key = (self.center_hz, self.span_hz)
        if self._band_cache[0] != key:
            lo, hi = self.center_hz - self.span_hz / 2, self.center_hz + self.span_hz / 2
            lanes = [(b, lane) for b, lane in assign_lanes(bands_in(lo, hi)) if lane < MAX_BAND_LANES]
            first = max((lane for _, lane in lanes), default=-1) + 1
            mesh = sorted((b for b in self._mesh_bands if b.hi * 1e6 > lo and b.lo * 1e6 < hi), key=lambda b: b.lo)
            # Leave room for labels beside narrow bars when stacking.
            gap = self.span_hz / 1e6 * 0.12
            lanes += [(b, first + lane) for b, lane in assign_lanes(mesh, min_gap=gap)]
            self._band_cache = (key, lanes)
        return self._band_cache[1]

    def _bands_height(self):
        lanes = self._band_lanes()
        return (max(lane for _, lane in lanes) + 1) * self._lane_height() + 3 if lanes else 0

    def _paint_bands(self, p, w):
        lane_h = self._lane_height()
        font = p.font()
        font.setFamily(self._mono)
        font.setPixelSize(max(8, self.font_px - 1))
        p.setFont(font)
        fm = p.fontMetrics()
        top = self.axis_height + 1
        for band, lane in self._band_lanes():
            x0 = max(0.0, self._freq_to_x(band.lo * 1e6, w))
            x1 = min(float(w), self._freq_to_x(band.hi * 1e6, w))
            x1 = max(x1, x0 + 2)
            r = QRectF(x0, top + lane * lane_h, x1 - x0, lane_h - 2)
            color = QColor(KIND_COLORS.get(band.kind, DIM))
            fill = QColor(color)
            live = band in self._mesh_bands and self._mesh_bands.index(band) in self.active
            fill.setAlphaF(0.40 if live else 0.14)
            p.fillRect(r, fill)
            p.fillRect(QRectF(r.left(), r.top(), r.width(), 1), color)
            # Only the band's real edges get a tick (not where the view clips it).
            edge = QColor(color)
            edge.setAlphaF(0.8)
            for f in (band.lo, band.hi):
                x = self._freq_to_x(f * 1e6, w)
                if 0 <= x <= w:
                    p.fillRect(QRectF(x - 0.5, r.top(), 1, r.height()), edge)
            p.setPen(color.lighter(130))
            if r.width() > fm.horizontalAdvance(band.label[:4]) + 10:
                p.drawText(r.adjusted(4, 1, -3, 0), Qt.AlignLeft | Qt.AlignVCenter,
                           fm.elidedText(band.label, Qt.ElideRight, int(r.width() - 7)))
            else:
                # Too narrow (e.g. a single Z-Wave channel or a mesh
                # profile): label it beside its marker -- right if there's
                # room in this lane, else left.
                tw = fm.horizontalAdvance(band.label)

                def free(a, b):
                    return 0 <= a and b <= w and not any(
                        l == lane and o is not band and self._freq_to_x(o.lo * 1e6, w) < b + 4
                        and self._freq_to_x(o.hi * 1e6, w) > a - 4 for o, l in self._band_lanes())

                for tx in (r.right() + 3, r.left() - 3 - tw):
                    if free(tx, tx + tw):
                        p.drawText(QRectF(tx, r.top() + 1, tw + 2, r.height()),
                                   Qt.AlignLeft | Qt.AlignVCenter, band.label)
                        break

    def mouseMoveEvent(self, event):
        if self.show_bands and self.span_hz > 1:
            hz = self._x_to_freq(event.position().x(), self.width())
            lines = [f"{hz / 1e6:.3f} MHz"]
            lines += [f"{b.label} ({b.lo:g}–{b.hi:g} MHz): {b.description}" for b in bands_at(hz)]
            lines += [f"{label} profile" for c, bw, label in self.channels if abs(hz - c) <= bw / 2]
            QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(TERMINAL_BG))
        spec_top = self.axis_height + self._bands_height()
        wf_top = spec_top + self.spectrum_height
        wf_rect = QRectF(0, wf_top, w, h - wf_top)

        if self._rows is not None:
            img_data = np.ascontiguousarray(self.lut[self._rows])
            img = QImage(img_data.data, img_data.shape[1], img_data.shape[0], img_data.shape[1] * 4, QImage.Format_ARGB32)
            # One image row per pixel; rows not yet received stay
            # background rather than showing the colormap's lowest colour.
            rows_shown = min(self._filled, max(1, int(wf_rect.height() / self.row_px)))
            dest = QRectF(0, wf_top, w, rows_shown * self.row_px)
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
            if self.trace_fill:
                area = QPainterPath(path)
                base = spec_top + self.spectrum_height
                area.lineTo(xs[-1], base)
                area.lineTo(xs[0], base)
                area.closeSubpath()
                fill = QColor(self.trace_color)
                fill.setAlphaF(0.08)
                p.fillPath(area, fill)
            p.setPen(QPen(QColor(self.trace_color), 1.2 if self.trace_fill else 2))
            p.drawPath(path)

        # LoRa channel band(s)
        if self.channels:
            self._paint_channels(p, w, h, spec_top)
        elif self.channel_bw_hz:
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
            (s for s in (100_000, 200_000, 250_000, 500_000, 1_000_000, 2_000_000) if w * s / self.span_hz >= label_px),
            2_000_000,
        )
        start = self.center_hz - self.span_hz / 2
        f = np.ceil(start / step) * step
        while f <= start + self.span_hz:
            x = self._freq_to_x(f, w)
            p.setPen(QColor("#9aa59a"))
            p.drawLine(int(x), self.axis_height - 4, int(x), self.axis_height)
            p.setPen(QColor(self.axis_text_color))
            p.drawText(QRectF(x - 50, 0, 100, self.axis_height - 4), Qt.AlignCenter, f"{f / 1e6:.3f}")
            f += step

        if self.show_bands:
            p.fillRect(QRectF(0, self.axis_height, w, spec_top - self.axis_height), QColor(TERMINAL_BG))
            self._paint_bands(p, w)

        if self._rows is None:
            p.setPen(QColor("#ffffff"))
            p.drawText(wf_rect, Qt.AlignCenter, self.idle_text)
        p.end()

    def _paint_channels(self, p, w, h, spec_top):
        """A band per channel across the spectrum and waterfall. The one(s)
        being listened to now: dashed green edges, faint green fill and a
        "name freq" tag; the rest are drawn fainter in white."""
        font = p.font()
        font.setFamily(self._mono)
        font.setPixelSize(max(8, self.font_px))
        p.setFont(font)
        for i, (center, bw, label) in enumerate(self.channels):
            live = i in self.active
            x0 = self._freq_to_x(center - bw / 2, w)
            x1 = max(x0 + 2, self._freq_to_x(center + bw / 2, w))
            if live:
                fill, edge, text = QColor(57, 255, 20, 15), QColor(57, 255, 20, 178), QColor(ACCENT_LIGHT)
            else:
                fill, edge, text = QColor(255, 255, 255, 8), QColor(255, 255, 255, 64), QColor(DIM)
            p.fillRect(QRectF(x0, spec_top, x1 - x0, h - spec_top), fill)
            p.setPen(QPen(edge, 1, Qt.DashLine))
            p.drawLine(int(x0), spec_top, int(x0), h)
            p.drawLine(int(x1), spec_top, int(x1), h)
            if self.show_bands and self._mesh_bands:
                continue  # named by its bar in the band lanes above
            tag = f"{label} {center / 1e6:.3f}"
            tw = p.fontMetrics().horizontalAdvance(tag) + 8
            th = self.font_px + 5
            tx = min(max(1, (x0 + x1) / 2 - tw / 2), w - tw - 1)
            box = QRectF(tx, spec_top + 2, tw, th)
            p.fillRect(box, QColor(0, 0, 0, 178))
            p.setPen(text)
            p.drawText(box, Qt.AlignCenter, tag)


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
