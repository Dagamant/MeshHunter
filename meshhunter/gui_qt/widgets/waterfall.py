"""RADIO panel: a per-frequency RSSI waterfall for the LoRa sniffer.

The data handling (per-column noise scaling, hop-mode stale cells, packet
markers) lives in core/waterfall_model.py, shared with the TUI; this file
only paints it. Packets are drawn as bright bars across their column
(white = good packet, amber = bad CRC, red = partial).
"""

import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from ...core.rtlsdr import SweepWaterfallWorker, rtlsdr_device_count
from ...core.waterfall_model import NOISE_HEADROOM_DB, ROWS_PER_SECOND, SCALE_SPAN_DB, WaterfallModel
from ..theme import ACCENT, DANGER_HOVER_BORDER, DIM, INK, LINE, LINE_SOFT, LOG_WARN, PANEL, TXT, pick_mono_font
from .common import section_label

ROW_H = 2
HEADER_H = 36
LEGEND_H = 18
TIME_AXIS_W = 34

# Dark -> logo green -> near white.
_STOPS = [
    (0.00, QColor("#000000")),
    (0.25, QColor("#08240e")),
    (0.50, QColor("#1f7a0e")),
    (0.75, QColor(ACCENT)),
    (1.00, QColor("#f2ffd8")),
]
_MARKER_COLORS = {"ok": QColor("#ffffff"), "crc": QColor(LOG_WARN), "partial": QColor(DANGER_HOVER_BORDER)}


def _lerp_color(t):
    t = min(1.0, max(0.0, t))
    for (t0, c0), (t1, c1) in zip(_STOPS, _STOPS[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return QColor(
                int(c0.red() + (c1.red() - c0.red()) * f),
                int(c0.green() + (c1.green() - c0.green()) * f),
                int(c0.blue() + (c1.blue() - c0.blue()) * f),
            )
    return _STOPS[-1][1]


# 256-entry lookup so painting never interpolates per cell.
_PALETTE = [_lerp_color(i / 255) for i in range(256)]


class WaterfallView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(HEADER_H + LEGEND_H + 60)
        self._mono = pick_mono_font()
        self.model = WaterfallModel()

    def set_profiles(self, profiles):
        self.model.set_profiles(profiles)
        self.update()

    def add_rssi(self, payload):
        self.model.add_rssi(payload)
        self.update()

    def add_event(self, payload):
        self.model.add_event(payload)

    # ---- painting ---------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(INK))
        model = self.model
        n = len(model.profiles)
        w, h = self.width(), self.height()
        small = QFont(self._mono)
        small.setPixelSize(10)
        p.setFont(small)

        if not n:
            p.setPen(QColor(DIM))
            p.drawText(self.rect(), Qt.AlignCenter, "Connect a LoRa sniffer to see what it hears")
            return

        body_top = HEADER_H
        body_bottom = h - LEGEND_H
        body_h = max(0, body_bottom - body_top)
        plot_w = w - TIME_AXIS_W
        gap = 3
        col_w = max(4.0, (plot_w - gap * (n - 1)) / n)

        # Column headers.
        for i, prof in enumerate(model.profiles):
            x = i * (col_w + gap)
            rect = QRectF(x + 2, 0, col_w - 4, HEADER_H - 4)
            is_cur = i in model.current
            p.setPen(QColor(ACCENT if is_cur else TXT))
            name = p.fontMetrics().elidedText(model.column_name(i), Qt.ElideRight, int(rect.width()))
            p.drawText(rect, Qt.AlignLeft | Qt.AlignTop, name)
            last = model.last[i]
            sub = f"{prof['freq']:g} MHz  {last:.0f} dBm" if last is not None else f"{prof['freq']:g} MHz"
            if model.counts[i]:
                sub += f"  ·{model.counts[i]} pkt"
            p.setPen(QColor(DIM))
            sub = p.fontMetrics().elidedText(sub, Qt.ElideRight, int(rect.width()))
            p.drawText(rect, Qt.AlignLeft | Qt.AlignBottom, sub)
            p.fillRect(QRectF(x, HEADER_H - 3, col_w, 2), QColor(ACCENT if is_cur else LINE))

        # Rows, newest at the top.
        visible = min(len(model.rows), body_h // ROW_H)
        for r in range(visible):
            y = body_top + r * ROW_H
            for i, (dbm, live, marker) in enumerate(model.rows[r]):
                x = i * (col_w + gap)
                if marker:
                    p.fillRect(QRectF(x, y, col_w, ROW_H), _MARKER_COLORS[marker])
                elif dbm is not None:
                    c = _PALETTE[int(model.level(i, dbm) * 255)]
                    if not live:
                        c = c.darker(170)
                    p.fillRect(QRectF(x, y, col_w, ROW_H), c)

        # Time axis.
        p.setPen(QPen(QColor(LINE)))
        axis_x = plot_w + 4
        p.drawLine(int(axis_x), body_top, int(axis_x), body_bottom)
        step_s = 5 if body_h // ROW_H < ROWS_PER_SECOND * 60 else 15
        rows_per_step = ROWS_PER_SECOND * step_s
        r = rows_per_step
        while r * ROW_H < body_h:
            y = body_top + r * ROW_H
            p.setPen(QColor(LINE))
            p.drawLine(int(axis_x), y, int(axis_x) + 3, y)
            p.setPen(QColor(DIM))
            p.drawText(QRectF(axis_x + 5, y - 7, TIME_AXIS_W - 6, 14), Qt.AlignLeft | Qt.AlignVCenter,
                       f"-{r // ROWS_PER_SECOND}s")
            r += rows_per_step

        # Legend: color scale + marker key.
        ly = body_bottom + 4
        bar = QRectF(0, ly + 2, 90, 8)
        grad = QLinearGradient(bar.topLeft(), bar.topRight())
        for t, c in _STOPS:
            grad.setColorAt(t, c)
        p.fillRect(bar, grad)
        p.setPen(QColor(DIM))
        p.drawText(QRectF(94, ly - 1, 120, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   f"noise … +{SCALE_SPAN_DB - NOISE_HEADROOM_DB} dB")
        x = 214
        for kind, label in (("ok", "packet"), ("crc", "bad CRC"), ("partial", "partial")):
            p.fillRect(QRectF(x, ly + 2, 10, 8), _MARKER_COLORS[kind])
            p.drawText(QRectF(x + 13, ly - 1, 60, 14), Qt.AlignLeft | Qt.AlignVCenter, label)
            x += 72


RADIO_STATE_COLORS = {
    "listening": ACCENT, "idle": DIM, "connecting": LOG_WARN, "retrying": LOG_WARN,
    "failed": DANGER_HOVER_BORDER, "stopped": DIM,
}


def radio_chip_html(radio):
    color = RADIO_STATE_COLORS.get(radio["state"], DIM)
    if radio["state"] == "listening":
        profiles = radio["profiles"]
        what = profiles[0] if len(profiles) == 1 else f"hopping {len(profiles)}"
        if radio.get("noise") is not None:
            what += f" · {radio['noise']:.0f} dBm"
    elif radio["state"] == "idle":
        what = "idle (no profile left)"
    else:
        what = radio["state"]
    return (f"<span style='color:{color}'>●</span> <b>{radio['label']}</b> "
            f"<span style='color:{DIM}'>{radio['kind']}</span> · <span style='color:{color}'>{what}</span>")


class WaterfallPanel(QWidget):
    """RADIO panel: waterfall on the left, signal/noise graph on the right,
    half the width each.

    The waterfall is the sniffer's own per-profile RSSI view unless an
    RTL-SDR is plugged in, in which case it switches to a real spectrum
    waterfall swept across every frequency the sniffer listens on (see
    core/rtlsdr.SweepWaterfallWorker), and back again if the dongle is
    unplugged or fails. Detection is a cheap device count every few
    seconds while the sniffer is live."""

    # Emitted for the terminal log (connected by MainWindow).
    log = Signal(str)
    # Thread -> GUI hops for the SDR worker; payloads carry a generation
    # number so rows from a just-stopped worker are dropped.
    _sdr_row = Signal(object)
    _sdr_error = Signal(object)

    POLL_MS = 3000
    RETRY_AFTER_ERROR_S = 30
    EDGE_MARGIN_HZ = 100_000  # shown either side of the outermost channels

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("waterfallPanel")
        mono = pick_mono_font()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.addWidget(section_label("RADIO"))
        self.source_label = QLabel("")
        self.source_label.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
        header.addWidget(self.source_label)
        header.addStretch(1)
        self.mode_label = QLabel("")
        self.mode_label.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
        header.addWidget(self.mode_label)
        layout.addLayout(header)

        # One status chip per radio: is it there, listening, and to what.
        self.radio_row = QHBoxLayout()
        self.radio_row.setSpacing(8)
        self.radio_row.addStretch(1)
        layout.addLayout(self.radio_row)
        self._radio_chips = []
        self._mono = mono

        # Waterfall (sniffer RSSI or real RTL-SDR) on the left, the
        # noise-floor-over-time graph on the right; equal stretch so each
        # gets half the width.
        from .sdr_waterfall import WaterfallCanvas
        from .sniffer_stats import NoiseGraph

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        self.view = WaterfallView()
        self.sdr_canvas = WaterfallCanvas(spectrum_height=56)
        self.sdr_canvas.setMinimumHeight(0)
        self.sdr_canvas.idle_text = "starting RTL-SDR…"
        self.wf_stack = QStackedWidget()
        self.wf_stack.addWidget(self.view)
        self.wf_stack.addWidget(self.sdr_canvas)
        for widget in (self.wf_stack, self.view, self.sdr_canvas):
            widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        body.addWidget(self.wf_stack, stretch=1)
        self.noise_graph = NoiseGraph()
        self.noise_graph.setMinimumWidth(0)
        self.noise_graph.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        body.addWidget(self.noise_graph, stretch=1)
        layout.addLayout(body, stretch=1)

        # RTL-SDR state.
        self._profiles = []
        self._sdr_key = None  # what the running sweep covers; retune if it changes
        self._sdr_worker = None
        self._sdr_gen = 0
        self._sdr_allowed = True  # False while LoRa HAT mode owns the dongle
        self._live = False  # a sniffer is connected and reporting
        self._retry_at = 0.0
        self._sdr_row.connect(self._on_sdr_row)
        self._sdr_error.connect(self._on_sdr_error)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_sdr)
        self._poll_timer.start(self.POLL_MS)
        self._show_source()

    # ---- sniffer feed ------------------------------------------------------

    def set_profiles(self, profiles):
        self._profiles = list(profiles)
        self._live = bool(self._profiles)
        self.sdr_canvas.set_channels([
            (p["freq"] * 1e6, p["bw"] * 1e3, p["name"]) for p in self._profiles
        ])
        if self._sdr_worker is not None and self._sweep_range() != self._sdr_key:
            self._stop_sdr()  # restarted on the new range below
        self._poll_sdr()
        self.view.set_profiles(profiles)
        radios = self.view.model.radio_count()
        if radios > 1:
            self.mode_label.setText(f"{radios} radios · {len(profiles)} profiles · highlighted = listening now")
        elif len(profiles) == 1:
            self.mode_label.setText("fixed · listening continuously")
        else:
            self.mode_label.setText(f"hopping {len(profiles)} profiles · highlighted = listening now")

    def set_radios(self, radios):
        while len(self._radio_chips) > len(radios):
            chip = self._radio_chips.pop()
            self.radio_row.removeWidget(chip)
            chip.deleteLater()
        while len(self._radio_chips) < len(radios):
            chip = QLabel()
            chip.setStyleSheet(
                f"font-family: '{self._mono}'; font-size: 11px; color: {TXT}; background: {PANEL};"
                f" border: 1px solid {LINE_SOFT}; border-radius: 6px; padding: 3px 8px;"
            )
            self.radio_row.insertWidget(len(self._radio_chips), chip)
            self._radio_chips.append(chip)
        for chip, radio in zip(self._radio_chips, radios):
            chip.setText(radio_chip_html(radio))
            tip = [f"{radio['kind']} on {radio['port']}", f"state: {radio['state']}"]
            if radio["detail"]:
                tip.append(radio["detail"])
            if radio["profiles"]:
                tip.append("profiles: " + ", ".join(radio["profiles"]))
            chip.setToolTip("\n".join(tip))

    def add_rssi(self, payload):
        self.view.add_rssi(payload)
        self.sdr_canvas.set_active(self.view.model.current)

    def add_event(self, payload):
        self.view.add_event(payload)
        self.noise_graph.add_event(payload)

    def add_stat(self, payload):
        self.noise_graph.add_stat(payload)

    def clear(self):
        self.view.set_profiles([])
        self.mode_label.setText("")
        self.set_radios([])
        self.noise_graph.clear()
        self._profiles = []
        self._live = False
        self._stop_sdr()

    # ---- RTL-SDR -----------------------------------------------------------

    def set_live(self, live):
        """False when the sniffer disconnects: frees the RTL-SDR (the last
        picture stays up). set_profiles() turns it back on."""
        self._live = live and bool(self._profiles)
        if not self._live:
            self._stop_sdr()

    def set_sdr_allowed(self, allowed):
        """LoRa HAT mode drives the same dongle for its own waterfall."""
        self._sdr_allowed = allowed
        if not allowed:
            self._stop_sdr()
        else:
            self._poll_sdr()

    def shutdown(self):
        self._poll_timer.stop()
        self._live = False
        self._stop_sdr()

    def _sweep_range(self):
        if not self._profiles:
            return None
        lo = min(p["freq"] * 1e6 - p["bw"] * 5e2 for p in self._profiles) - self.EDGE_MARGIN_HZ
        hi = max(p["freq"] * 1e6 + p["bw"] * 5e2 for p in self._profiles) + self.EDGE_MARGIN_HZ
        return (lo, hi)

    def _poll_sdr(self):
        if not (self._live and self._sdr_allowed):
            self._stop_sdr()
            return
        if self._sdr_worker is not None:
            return  # an unplug shows up as a worker error
        present = rtlsdr_device_count() > 0
        if not present:
            self._retry_at = 0.0  # unplugged: a replug starts straight away
        elif time.monotonic() >= self._retry_at:
            self._start_sdr()
        self._show_source()

    def _start_sdr(self):
        lo, hi = self._sweep_range()
        self._sdr_gen += 1
        gen = self._sdr_gen
        worker = SweepWaterfallWorker(
            lo, hi,
            on_row=lambda row: self._sdr_row.emit((gen, row)),
            on_error=lambda msg: self._sdr_error.emit((gen, msg)),
        )
        # A multi-segment sweep only manages ~3 rows/s: draw those taller.
        self.sdr_canvas.row_px = 2 if len(worker.centers) > 1 else 1
        self.sdr_canvas.configure(worker.center_hz, worker.span_hz, 0)
        self.sdr_canvas.set_active(self.view.model.current)
        self._sdr_worker = worker
        self._sdr_key = (lo, hi)
        worker.start()
        self.wf_stack.setCurrentWidget(self.sdr_canvas)
        segs = len(worker.centers)
        how = "" if segs == 1 else f", sweeping {segs} segments"
        self.log.emit(
            f"-- RTL-SDR found: real waterfall {worker.center_hz / 1e6:.3f} MHz "
            f"± {worker.span_hz / 2e6:.2f} MHz{how} --"
        )
        self._show_source()

    def _stop_sdr(self):
        worker, self._sdr_worker = self._sdr_worker, None
        self._sdr_key = None
        self._sdr_gen += 1
        if worker is not None:
            worker.stop()
            worker.join(timeout=1.5)
        self.wf_stack.setCurrentWidget(self.view)
        self._show_source()

    def _on_sdr_row(self, payload):
        gen, row = payload
        if gen == self._sdr_gen:
            self.sdr_canvas.add_row(row)

    def _on_sdr_error(self, payload):
        gen, msg = payload
        if gen != self._sdr_gen:
            return
        self._sdr_worker = None
        self._sdr_key = None
        self._retry_at = time.monotonic() + self.RETRY_AFTER_ERROR_S
        self.wf_stack.setCurrentWidget(self.view)
        self.log.emit(f"-- RTL-SDR error: {msg} (using sniffer RSSI waterfall) --")
        self._show_source()

    def _show_source(self):
        if self._sdr_worker is not None:
            text, color = "· RTL-SDR spectrum", ACCENT
        elif self._live:
            text, color = "· sniffer RSSI (no RTL-SDR)", DIM
        else:
            text, color = "", DIM
        self.source_label.setText(text)
        self.source_label.setStyleSheet(f"font-family: '{self._mono}'; font-size: 10px; color: {color};")
