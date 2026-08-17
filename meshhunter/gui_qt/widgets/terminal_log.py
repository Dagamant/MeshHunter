"""The terminal-style scrolling log view: header (title + Clear button),
tag-colored body, and a footer with a blinking prompt cursor.

Unlike the tkinter version, this needs no window-drag-selects-text
workaround (see main_tk.py's _on_terminal_press/_on_terminal_motion) --
that defended against a raw tk.Text-specific event-model bug under
XWayland that QPlainTextEdit doesn't have.
"""

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from ...core.constants import TERMINAL_MAX_LINES
from ...core.log_format import classify_log_line
from ..theme import (
    ACCENT,
    ACCENT_DIM,
    DIM,
    LOG_OK,
    LOG_PATH,
    LOG_WARN,
    RAIL_TOP,
    TERMINAL_BG,
    TERMINAL_DIVIDER,
    TXT,
    pick_mono_font,
)
from .common import make_button, section_label


class TerminalLog(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("terminalPanel")
        self._mono = pick_mono_font()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_body(), stretch=1)
        layout.addWidget(self._build_footer())

        self._cursor_on = True
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._blink_cursor)
        self._blink_timer.start(550)

    def _build_header(self):
        header = QFrame()
        header.setObjectName("terminalHeader")
        header.setFixedHeight(44)
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 0, 16, 0)

        title = section_label("SERIAL TERMINAL")
        title.setStyleSheet(title.styleSheet() + f" color: {ACCENT};")
        row.addWidget(title)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(f"background-color: {TERMINAL_DIVIDER}; max-height: 1px; border: none;")
        row.addWidget(divider, stretch=1)

        clear_btn = make_button("Clear", variant="secondary", height=26)
        clear_btn.clicked.connect(self.clear)
        row.addWidget(clear_btn)

        return header

    def _build_body(self):
        self.terminal = QPlainTextEdit()
        self.terminal.setObjectName("terminal")
        self.terminal.setReadOnly(True)
        self.terminal.setMaximumBlockCount(0)  # trimmed manually to match TERMINAL_MAX_LINES exactly
        self.terminal.setStyleSheet(f"font-family: '{self._mono}'; font-size: 12px;")

        self._formats = {}
        for tag, color in {
            "ts": ACCENT_DIM, "event": ACCENT, "key": DIM, "val": TXT,
            "path": LOG_PATH, "warn": LOG_WARN, "ok": LOG_OK, "dim": DIM,
            None: TXT,
        }.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            self._formats[tag] = fmt

        return self.terminal

    def _build_footer(self):
        footer = QFrame()
        footer.setStyleSheet(f"background-color: {TERMINAL_BG};")
        footer.setFixedHeight(26)
        row = QHBoxLayout(footer)
        row.setContentsMargins(16, 0, 16, 0)

        prompt = QLabel("❯")
        prompt.setStyleSheet(f"font-family: '{self._mono}'; font-size: 12px; color: {ACCENT};")
        row.addWidget(prompt)

        self.cursor_block = QLabel("")
        self.cursor_block.setFixedSize(8, 15)
        self.cursor_block.setStyleSheet(f"background-color: {ACCENT};")
        row.addWidget(self.cursor_block)
        row.addStretch(1)

        return footer

    def _blink_cursor(self):
        self._cursor_on = not self._cursor_on
        color = ACCENT if self._cursor_on else TERMINAL_BG
        self.cursor_block.setStyleSheet(f"background-color: {color};")

    def append_line(self, line):
        scrollbar = self.terminal.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2

        cursor = self.terminal.textCursor()
        cursor.movePosition(QTextCursor.End)
        for text, tag in classify_log_line(line):
            cursor.insertText(text, self._formats.get(tag, self._formats[None]))
        cursor.insertText("\n", self._formats[None])

        overflow = self.terminal.document().blockCount() - TERMINAL_MAX_LINES
        if overflow > 0:
            trim_cursor = QTextCursor(self.terminal.document())
            trim_cursor.movePosition(QTextCursor.Start)
            trim_cursor.movePosition(QTextCursor.NextBlock, QTextCursor.KeepAnchor, overflow)
            trim_cursor.removeSelectedText()

        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def clear(self):
        self.terminal.clear()
