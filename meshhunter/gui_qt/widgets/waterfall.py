"""RADIO panel: a per-frequency RSSI waterfall for the LoRa sniffer.

The data handling (per-column noise scaling, hop-mode stale cells, packet
markers) lives in core/waterfall_model.py, shared with the TUI; this file
only paints it. Packets are drawn as bright bars across their column
(white = good packet, amber = bad CRC, red = partial).
"""

import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.rtlsdr import SweepWaterfallWorker, rtlsdr_device_count
from ...core.waterfall_model import NOISE_HEADROOM_DB, ROWS_PER_SECOND, SCALE_SPAN_DB, WaterfallModel
from ..theme import ACCENT, DANGER_HOVER_BORDER, DIM, INK, LINE, LOG_WARN, TXT, pick_mono_font

ROW_H = 2
HEADER_H = 36
LEGEND_H = 18
TIME_AXIS_W = 34

# Black -> deep green -> logo green -> near white (the redesign's waterfall
# colormap, shared with the RTL-SDR canvas's GREEN_LUT).
_STOPS = [
    (0.00, QColor(0, 0, 0)),
    (0.32, QColor(2, 22, 9)),
    (0.55, QColor(10, 80, 22)),
    (0.78, QColor(57, 230, 20)),
    (1.00, QColor(225, 255, 205)),
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

    # Signal/noise graph width (px) the user dragged the splitter to.
    graph_width_changed = Signal(int)
    gain_changed = Signal(object)  # dB, or None for tuner AGC

    GRAPH_WIDTH = 380
    GAINS = (("Gain AGC", None), ("Gain 20 dB", 20.0), ("Gain 30 dB", 30.0), ("Gain 40 dB", 40.0),
             ("Gain 49 dB", 49.6))

    def __init__(self, parent=None, graph_width=None, gain_db=None):
        super().__init__(parent)
        self.setObjectName("rfBand")
        self.setAttribute(Qt.WA_StyledBackground, True)
        mono = pick_mono_font()
        self._mono = mono
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(6)

        # Header: RF · source/range · radio pill(s) · stretch · mode · gain.
        header = QHBoxLayout()
        header.setSpacing(10)
        rf = QLabel("RF")
        rf.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; font-weight: 600; color: {ACCENT};")
        header.addWidget(rf)
        self.source_label = QLabel("")
        self.source_label.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; color: {DIM};")
        header.addWidget(self.source_label)
        # One status pill per radio: is it there, listening, and to what.
        self.radio_row = QHBoxLayout()
        self.radio_row.setSpacing(6)
        header.addLayout(self.radio_row)
        self._radio_chips = []
        header.addStretch(1)
        self.mode_label = QLabel("")
        self.mode_label.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; color: {DIM};")
        header.addWidget(self.mode_label)
        self.gain_combo = QComboBox()
        for label, value in self.GAINS:
            self.gain_combo.addItem(label, value)
        idx = next((i for i, (_, v) in enumerate(self.GAINS) if v == gain_db), 0)
        self.gain_combo.setCurrentIndex(idx)
        self.gain_combo.setFixedHeight(22)
        self.gain_combo.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; padding: 0 8px; border-radius: 7px;")
        self.gain_combo.setToolTip("RTL-SDR tuner gain")
        self.gain_combo.currentIndexChanged.connect(self._on_gain_changed)
        self.gain_combo.setVisible(False)  # only with an RTL-SDR running
        header.addWidget(self.gain_combo)
        layout.addLayout(header)

        # Waterfall (sniffer RSSI or real RTL-SDR) | signal/noise graph, in a
        # splitter; the graph starts at GRAPH_WIDTH and keeps what it's
        # dragged to.
        from .sdr_waterfall import GREEN_LUT, WaterfallCanvas
        from .sniffer_stats import NoiseGraph

        self.view = WaterfallView()
        self.sdr_canvas = WaterfallCanvas(spectrum_height=40, font_px=9, axis_height=14, lut=GREEN_LUT,
                                          trace_color=LOG_WARN, trace_fill=True, show_bands=True)
        self.sdr_canvas.setMinimumHeight(0)
        self.sdr_canvas.idle_text = "starting RTL-SDR…"
        self.wf_stack = QStackedWidget()
        self.wf_stack.addWidget(self.view)
        self.wf_stack.addWidget(self.sdr_canvas)
        for widget in (self.view, self.sdr_canvas):
            widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.wf_stack.setMinimumWidth(200)
        self.noise_graph = NoiseGraph()
        self.noise_graph.setMinimumWidth(160)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        self.splitter.addWidget(self.wf_stack)
        self.splitter.addWidget(self.noise_graph)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self._graph_width = graph_width or self.GRAPH_WIDTH
        self._graph_width_applied = False
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        layout.addWidget(self.splitter, stretch=1)

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

    def showEvent(self, event):
        super().showEvent(event)
        if not self._graph_width_applied and self.splitter.width() > 0:
            self._apply_graph_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_graph_width()

    def _apply_graph_width(self):
        total = self.splitter.width() - self.splitter.handleWidth()
        if total <= 0:
            return
        graph = min(self._graph_width, max(160, total - 200))
        self.splitter.setSizes([total - graph, graph])
        self._graph_width_applied = True

    def _on_splitter_moved(self, *_):
        self._graph_width = self.splitter.sizes()[1]
        self.graph_width_changed.emit(self._graph_width)

    def _on_gain_changed(self, *_):
        gain = self.gain_combo.currentData()
        self.gain_changed.emit(gain)
        if self._sdr_worker is not None:
            self._stop_sdr()
            self._poll_sdr()

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
            self.mode_label.setText(f"{radios} radios · {len(profiles)} profiles")
        elif len(profiles) == 1:
            self.mode_label.setText("Fixed · listening continuously")
        elif profiles:
            self.mode_label.setText(f"Hop · {len(profiles)} profiles · highlighted = listening now")
        self._show_source()

    def set_radios(self, radios):
        while len(self._radio_chips) > len(radios):
            chip = self._radio_chips.pop()
            self.radio_row.removeWidget(chip)
            chip.deleteLater()
        while len(self._radio_chips) < len(radios):
            chip = QLabel()
            chip.setStyleSheet(
                f"font-family: '{self._mono}'; font-size: 11px; color: {TXT}; background: transparent;"
                f" border: 1px solid {LINE}; border-radius: 9px; padding: 1px 8px;"
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
            gain_db=self.gain_combo.currentData(),
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
        worker = self._sdr_worker
        if worker is not None:
            lo = (worker.center_hz - worker.span_hz / 2) / 1e6
            hi = (worker.center_hz + worker.span_hz / 2) / 1e6
            text = f"RTL-SDR · {lo:.3f} – {hi:.3f} MHz"
        elif self._live:
            text = f"sniffer RSSI · {len(self._profiles)} profile" + ("" if len(self._profiles) == 1 else "s")
            text += " · no RTL-SDR"
        else:
            text = ""
        self.source_label.setText(text)
        self.gain_combo.setVisible(worker is not None)
