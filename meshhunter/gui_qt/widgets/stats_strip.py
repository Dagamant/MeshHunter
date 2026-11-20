"""The 34 px stats strip under the top bar (redesign 1a): live sniffer
telemetry, node counts and the session timer in one row of numbers.

Merges what SnifferStatsBar (sniffer_stats.py) and NodePanel (node_panel.py)
showed as tiles; those stay for the pocket layout. Same APIs as both:
add_stat/clear for telemetry, update_counts/reset for nodes.
"""

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from ...core.constants import MESHTASTIC_COUNT_KEY, NODE_DISPLAY_TYPES
from ..theme import ACCENT, DIM, LINE, MESH_MESHTASTIC, TXT, pick_mono_font


class _Item(QWidget):
    """DIM uppercase label, 7 px gap, value."""

    def __init__(self, label, value="—", suffix_label=False, parent=None):
        super().__init__(parent)
        self._mono = pick_mono_font()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        self.label = QLabel(label)
        self.value = QLabel(value)
        if suffix_label:  # "171 chat": value first, lowercase label after
            row.addWidget(self.value)
            row.addWidget(self.label)
        else:
            row.addWidget(self.label)
            row.addWidget(self.value)
        self.label.setStyleSheet(f"font-family: '{self._mono}'; font-size: 11px; color: {DIM};")
        self.set_value(value)

    def set_value(self, text, color=None):
        self.value.setText(str(text))
        if color is None:
            color = TXT if str(text) in ("0", "—", "") else ACCENT
        self.value.setStyleSheet(f"font-family: '{self._mono}'; font-size: 11px; color: {color};")


class StatsStrip(QFrame):
    HEIGHT = 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statsStrip")
        self.setFixedHeight(self.HEIGHT)
        self._latest = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(22)

        # Sniffer telemetry -- only while a sniffer reports it.
        self.noise = _Item("NOISE")
        self.rx = _Item("RX")
        self.rx_err = _Item("RX ERR")
        self.rx_err.setToolTip("bad CRC + header errors + timeouts")
        self.pre = _Item("PREAMBLES")
        self.hops = _Item("HOPS")
        self._sniffer_items = [self.noise, self.rx, self.rx_err, self.pre, self.hops]
        for item in self._sniffer_items:
            row.addWidget(item)
        self.sniffer_divider = QFrame()
        self.sniffer_divider.setFixedSize(1, 16)
        self.sniffer_divider.setStyleSheet(f"background-color: {LINE}; border: none;")
        row.addWidget(self.sniffer_divider)

        nodes = QWidget()
        nrow = QHBoxLayout(nodes)
        nrow.setContentsMargins(0, 0, 0, 0)
        nrow.setSpacing(14)
        mono = pick_mono_font()
        title = QLabel("NODES")
        title.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; color: {DIM};")
        nrow.addWidget(title)
        self._nodes = {}
        for node_type, label in NODE_DISPLAY_TYPES:
            item = _Item(label.lower(), "0", suffix_label=True)
            self._nodes[node_type] = item
            nrow.addWidget(item)
        mt = _Item("meshtastic", "0", suffix_label=True)
        self._nodes[MESHTASTIC_COUNT_KEY] = mt
        nrow.addWidget(mt)
        row.addWidget(nodes)
        self._node_items = list(self._nodes.values())

        row.addStretch(1)
        self.session = _Item("SESSION", "--:--:--")
        row.addWidget(self.session)
        self._session_start = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        # Hidden first when the window narrows: rightmost node counts, then
        # the less important sniffer counters.
        self._shed_order = [self._nodes[MESHTASTIC_COUNT_KEY], self._nodes[node_type_last()],
                            self.hops, self.pre, self.session]
        self._set_sniffer_visible(False)
        self.update_counts({k: 0 for k in self._nodes})

    # ---- sniffer telemetry (SnifferStatsBar API) ----------------------------

    def add_stat(self, payload):
        self._latest[payload.get("radio") or ""] = payload
        stats = self._latest.values()
        ok = sum(p.get("ok") or 0 for p in stats)
        bad = sum(p.get("bad") or 0 for p in stats)
        pre = sum(p.get("pre") or 0 for p in stats)
        hops = sum(p.get("hops") or 0 for p in stats)
        noises = [p["noise"] for p in stats if p.get("noise") is not None]
        self.noise.set_value(f"{min(noises):.0f} dBm" if noises else "—", ACCENT if noises else TXT)
        self.rx.set_value(ok)
        self.rx_err.set_value(bad)
        self.pre.set_value(pre)
        self.hops.set_value(hops)
        if len(self._latest) > 1 and noises:
            self.noise.setToolTip("  ·  ".join(
                f"{p['radio']}: {p['noise']:.0f} dBm" for p in stats if p.get("noise") is not None))
        self._set_sniffer_visible(True)

    def clear(self):
        self._latest.clear()
        for item in self._sniffer_items:
            item.set_value("—")
        self._set_sniffer_visible(False)

    def _set_sniffer_visible(self, visible):
        self._sniffer_shown = visible
        for item in self._sniffer_items:
            item.setVisible(visible)
        self.sniffer_divider.setVisible(visible)
        self._shed()

    # ---- nodes (NodePanel API) ----------------------------------------------

    def update_counts(self, counts):
        for node_type, count in counts.items():
            item = self._nodes.get(node_type)
            if item is None:
                continue
            color = None
            if node_type == MESHTASTIC_COUNT_KEY and count:
                color = MESH_MESHTASTIC
            item.set_value(count, color)

    def reset(self):
        self.update_counts({k: 0 for k in self._nodes})

    # ---- session timer ------------------------------------------------------

    def start_session(self):
        self._session_start = time.monotonic()
        self._tick()

    def stop_session(self):
        self._session_start = None
        self.session.set_value("--:--:--", DIM)

    def _tick(self):
        if self._session_start is None:
            return
        s = int(time.monotonic() - self._session_start)
        self.session.set_value(f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}", TXT)

    # ---- narrowing ----------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._shed()

    def _shed(self):
        """No wrapping: hide items (in _shed_order) until the row fits."""
        for item in self._shed_order:
            if item in self._sniffer_items:
                item.setVisible(self._sniffer_shown)
            else:
                item.setVisible(True)
        layout = self.layout()
        for item in self._shed_order:
            if layout.sizeHint().width() <= self.width() or self.width() == 0:
                break
            item.setVisible(False)


def node_type_last():
    return NODE_DISPLAY_TYPES[-1][0]
