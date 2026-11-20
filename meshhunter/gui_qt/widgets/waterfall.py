"""RADIO panel: a per-frequency RSSI waterfall for the LoRa sniffer.

The data handling (per-column noise scaling, hop-mode stale cells, packet
markers) lives in core/waterfall_model.py, shared with the TUI; this file
only paints it. Packets are drawn as bright bars across their column
(white = good packet, amber = bad CRC, red = partial).
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("waterfallPanel")
        mono = pick_mono_font()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.addWidget(section_label("RADIO"))
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

        # Waterfall on the left, a noise-floor-over-time graph beside it.
        from .sniffer_stats import NoiseGraph

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        self.view = WaterfallView()
        self.view.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.view.setFixedWidth(200)
        body.addWidget(self.view)
        self.noise_graph = NoiseGraph()
        body.addWidget(self.noise_graph, stretch=1)
        layout.addLayout(body, stretch=1)

    def set_profiles(self, profiles):
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
