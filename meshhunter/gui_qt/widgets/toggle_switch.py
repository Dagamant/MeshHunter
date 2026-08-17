"""A small track+knob toggle switch. Qt has no built-in equivalent to
customtkinter's CTkSwitch, so this is a minimal custom-painted QAbstractButton
standing in for it -- checkable, click-to-toggle, animated-free (a plain
color swap on toggle is enough here, no need for the extra complexity of an
animated slide)."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton

from ..theme import ACCENT, LINE_SOFT, SWITCH_KNOB_OFF, SWITCH_TRACK_OFF, SWITCH_TRACK_ON


class ToggleSwitch(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(34, 18)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        on = self.isChecked()
        track_color = QColor(SWITCH_TRACK_ON if on else SWITCH_TRACK_OFF)
        border_color = QColor(ACCENT if on else LINE_SOFT)
        knob_color = QColor(ACCENT if on else SWITCH_KNOB_OFF)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(border_color)
        painter.setBrush(track_color)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        knob_d = rect.height() - 6
        knob_x = rect.right() - knob_d - 3 if on else rect.left() + 3
        knob_rect = QRectF(knob_x, rect.top() + 3, knob_d, knob_d)
        painter.setPen(Qt.NoPen)
        painter.setBrush(knob_color)
        painter.drawEllipse(knob_rect)
