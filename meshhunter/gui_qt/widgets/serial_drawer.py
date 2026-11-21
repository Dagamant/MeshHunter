"""Serial drawer (redesign 1a): the raw terminal log tucked into a 30 px
bar at the bottom of the window, previewing its latest line. Clicking the
bar (or Ctrl+`) opens it to the full TerminalLog.

Has TerminalLog's append_line/clear, so the window logs to it the same way.
MainWindow owns the open height (it lives in the window's splitter).
"""

import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..theme import ACCENT, DIM, LINE, LOG_OK, LOG_PATH, TXT, pick_mono_font
from .terminal_log import TerminalLog

BAR_HEIGHT = 30
_PREVIEW_RE = re.compile(r"^(\[[^\]]*\]\s*)?(\[[^\]]*\]\s*)?([A-Z][A-Z0-9_]+:)?(.*)$")


class _Bar(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class SerialDrawer(QWidget):
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mono = pick_mono_font()
        self._open = False
        self._lines = 0
        self._last = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.bar = _Bar()
        self.bar.setObjectName("serialBar")
        self.bar.setFixedHeight(BAR_HEIGHT)
        self.bar.setCursor(Qt.PointingHandCursor)
        self.bar.clicked.connect(self.toggle)
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(10)
        self.arrow = self._lbl("▸", DIM)
        row.addWidget(self.arrow)
        title = self._lbl("SERIAL", ACCENT)
        title.setStyleSheet(title.styleSheet() + " font-weight: 600; letter-spacing: 0.06em;")
        row.addWidget(title)
        self.count = self._lbl("0 lines", DIM)
        row.addWidget(self.count)
        sep = QFrame()
        sep.setFixedSize(1, 14)
        sep.setStyleSheet(f"background-color: {LINE}; border: none;")
        row.addWidget(sep)
        self.preview = self._lbl("", TXT)
        self.preview.setTextFormat(Qt.RichText)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.preview, stretch=1)
        self.hint = self._lbl("Ctrl+` to open", DIM)
        row.addWidget(self.hint)
        lay.addWidget(self.bar)

        self.log = TerminalLog()
        self.log.setVisible(False)
        lay.addWidget(self.log, stretch=1)
        self.terminal = self.log.terminal
        self._update_bar()

    def _lbl(self, text, color):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-family: '{self._mono}'; font-size: 11px; color: {color};")
        lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        return lbl

    # ---- TerminalLog API ---------------------------------------------------

    def append_line(self, line):
        self.log.append_line(line)
        self._lines += 1
        self._last = line
        self._update_bar()

    def clear(self):
        self.log.clear()
        self._lines = 0
        self._last = ""
        self._update_bar()

    # ---- open/close ----------------------------------------------------------

    def is_open(self):
        return self._open

    def toggle(self):
        self.set_open(not self._open)

    def set_open(self, open_):
        if open_ == self._open:
            return
        self._open = open_
        self.log.setVisible(open_)
        self.setMaximumHeight(16777215 if open_ else BAR_HEIGHT)
        self.setMinimumHeight(BAR_HEIGHT + 90 if open_ else BAR_HEIGHT)
        self._update_bar()
        self.toggled.emit(open_)

    # ---- bar ----------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_preview()

    def _update_bar(self):
        self.arrow.setText("▾" if self._open else "▸")
        self.hint.setText("Ctrl+` to close" if self._open else "Ctrl+` to open")
        self.count.setText(f"{self._lines} line" + ("" if self._lines == 1 else "s"))
        self._update_preview()

    def _update_preview(self):
        if not self._last or self._open:
            self.preview.setText("")
            return
        m = _PREVIEW_RE.match(self._last)
        ts = (m.group(2) or m.group(1) or "").strip().strip("[]")
        event, rest = m.group(3) or "", m.group(4) or ""
        # Elide on the plain text, then colour it.
        fm = self.preview.fontMetrics()
        budget = max(0, self.preview.width() - fm.horizontalAdvance(f"{ts} {event} "))
        rest = fm.elidedText(rest.strip(), Qt.ElideRight, budget)
        self.preview.setText(
            (f"<span style='color:{LOG_PATH}'>{html.escape(ts)}</span>&nbsp;" if ts else "")
            + (f"<span style='color:{LOG_OK}'>{html.escape(event)}</span>&nbsp;" if event else "")
            + f"<span style='color:{TXT}'>{html.escape(rest)}</span>"
        )
