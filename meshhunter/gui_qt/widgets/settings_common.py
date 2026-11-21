"""Building blocks shared by the Settings pages (redesign 1e): page header,
hints, link buttons, cards, the 14 px check box and a two-way segmented
choice. Each page is a SettingsPage: it has a nav title, a live nav
summary, and emits `changed` whenever one of its fields is edited.
"""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..theme import (
    ACCENT,
    ACCENT_DIM,
    CHECK_OFF_BORDER,
    DIM,
    LINE,
    PANEL,
    SWITCH_TRACK_ON,
    TEXT_SECONDARY,
    TXT,
    pick_mono_font,
    pick_sans_font,
)
from .toggle_switch import ToggleSwitch
from .top_bar import Segmented


def mono_style(px, color=TXT, extra=""):
    return f"font-family: '{pick_mono_font()}'; font-size: {px}px; color: {color}; {extra}"


def sans_style(px, color=TXT, weight=400, extra=""):
    return f"font-family: '{pick_sans_font()}'; font-size: {px}px; font-weight: {weight}; color: {color}; {extra}"


def label(text, style, wrap=False):
    lbl = QLabel(text)
    lbl.setStyleSheet(style)
    lbl.setWordWrap(wrap)
    return lbl


def hint(text, wrap=True):
    """Small DIM explanatory text."""
    return label(text, mono_style(10.5, DIM), wrap)


def caption(text):
    """Uppercase mono section caption ("HASHTAG", "PRIVATE", ...)."""
    return label(text, mono_style(10.5, DIM, "letter-spacing: 0.06em;"))


def link(text, slot=None):
    btn = QToolButton()
    btn.setText(text)
    btn.setProperty("class", "Link")
    btn.setCursor(Qt.PointingHandCursor)
    font = QFont(pick_mono_font())  # colours come from the Link QSS
    font.setPixelSize(11)
    btn.setFont(font)
    if slot is not None:
        btn.clicked.connect(slot)
    return btn


def close_button(slot=None, tooltip="Remove"):
    btn = QToolButton()
    btn.setText("×")
    btn.setProperty("class", "Close")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip(tooltip)
    if slot is not None:
        btn.clicked.connect(slot)
    return btn


def toggle_row(text, hint_text=""):
    """[switch] Label  hint -> (row widget, ToggleSwitch)."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    switch = ToggleSwitch()
    lay.addWidget(switch)
    lay.addWidget(label(text, sans_style(13, TXT, 500)))
    if hint_text:
        lay.addWidget(hint(hint_text, wrap=False))
    lay.addStretch(1)
    return row, switch


class Card(QFrame):
    """PANEL background, LINE border, radius 10, padding 12x14."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#settingsCard {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px; }}"
                           " #settingsCard QWidget#cardInner { background: transparent; }")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(10)


class CheckMark(QAbstractButton):
    """14 px check box: SWITCH_TRACK_ON fill + ACCENT tick when on,
    transparent with a CHECK_OFF_BORDER outline when off."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(14, 14)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, 13, 13)
        on = self.isChecked()
        p.setPen(QPen(QColor(ACCENT_DIM if on else CHECK_OFF_BORDER), 1))
        p.setBrush(QColor(SWITCH_TRACK_ON) if on else Qt.NoBrush)
        if not self.isEnabled():
            p.setOpacity(0.6)
        p.drawRoundedRect(r, 3, 3)
        if on:
            p.setPen(QPen(QColor(ACCENT), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPolyline([QPointF(3.5, 7.5), QPointF(6.1, 10.1), QPointF(10.9, 4.7)])


class Choice(Segmented):
    """Exclusive segmented choice between `options` [(key, label), ...]."""

    changed = Signal(str)

    def __init__(self, options, height=32, parent=None):
        super().__init__([lbl for _, lbl in options], height=height, checkable=True, parent=parent)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._keys = [k for k, _ in options]
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, btn in enumerate(self.buttons):
            self.group.addButton(btn, i)
        self.group.idClicked.connect(lambda i: self.changed.emit(self._keys[i]))
        self.buttons[0].setChecked(True)

    def value(self):
        return self._keys[max(0, self.group.checkedId())]

    def set_value(self, key):
        if key in self._keys:
            self.buttons[self._keys.index(key)].setChecked(True)


class SettingsPage(QWidget):
    """A page of the Settings dialog. Subclasses set TITLE/DESCRIPTION,
    add their fields to self.body and implement summary()/values()."""

    TITLE = ""
    NAV_TITLE = ""
    DESCRIPTION = ""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)
        outer.addWidget(label(self.TITLE, sans_style(18, TXT, 600)))
        if self.DESCRIPTION:
            desc = label(self.DESCRIPTION, sans_style(13, TEXT_SECONDARY, extra="line-height: 150%;"), wrap=True)
            desc.setMaximumWidth(560)
            outer.addWidget(desc)
        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        outer.addLayout(self.body)
        outer.addStretch(1)

    def summary(self):
        return ""

    def values(self):
        return {}
