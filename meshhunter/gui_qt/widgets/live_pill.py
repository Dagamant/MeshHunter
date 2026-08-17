"""The pulsing "LIVE" status pill in the brand header."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..theme import ACCENT, ACCENT_DIM, ACCENT_LIGHT, LINE, LIVE_PILL_BG, pick_mono_font


class LivePill(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        mono = pick_mono_font()
        self._connected = False
        self._on = False

        self.setStyleSheet(
            f"background-color: {LIVE_PILL_BG}; border: 1px solid {LINE}; border-radius: 99px;"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 3, 9, 3)
        layout.setSpacing(5)

        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"font-family: '{mono}'; font-size: 8px; color: {ACCENT_DIM}; border: none;")
        layout.addWidget(self.dot)

        label = QLabel("LIVE")
        label.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {ACCENT_LIGHT}; border: none;")
        layout.addWidget(label)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._pulse)
        self._timer.start(900)

    def set_connected(self, connected):
        self._connected = connected
        if not connected:
            self.dot.setStyleSheet(f"font-family: monospace; font-size: 8px; color: {ACCENT_DIM}; border: none;")

    def _pulse(self):
        if not self._connected:
            return
        self._on = not self._on
        color = ACCENT if self._on else ACCENT_DIM
        self.dot.setStyleSheet(f"font-size: 8px; color: {color}; border: none;")
