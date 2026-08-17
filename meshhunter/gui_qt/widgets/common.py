"""Small shared widget-building helpers used across the rail panels."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ..theme import DIM, LINE_SOFT, pick_mono_font
from .toggle_switch import ToggleSwitch


def make_button(text, variant="secondary", height=34):
    btn = QPushButton(text)
    btn.setProperty("variant", variant)
    btn.setMinimumHeight(height)
    btn.setCursor(Qt.PointingHandCursor)
    return btn


def section_label(text):
    mono = pick_mono_font()
    lbl = QLabel(text)
    lbl.setProperty("class", "SectionLabel")
    lbl.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
    return lbl


def divider():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"background-color: {LINE_SOFT}; max-height: 1px; border: none;")
    return line


def labeled_entry(label, password=False):
    """Returns (wrapper_widget, QLineEdit)."""
    mono = pick_mono_font()
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 10)
    layout.setSpacing(5)

    lbl = QLabel(label)
    lbl.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
    layout.addWidget(lbl)

    entry = QLineEdit()
    entry.setStyleSheet(f"font-family: '{mono}'; font-size: 12px;")
    entry.setMinimumHeight(32)
    if password:
        entry.setEchoMode(QLineEdit.Password)
    layout.addWidget(entry)

    return wrapper, entry


def labeled_switch(label):
    """Returns (row_widget, ToggleSwitch)."""
    mono = pick_mono_font()
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 10)
    layout.setSpacing(8)

    switch = ToggleSwitch()
    layout.addWidget(switch)

    lbl = QLabel(label)
    lbl.setStyleSheet(f"font-family: '{mono}'; font-size: 12px;")
    layout.addWidget(lbl)
    layout.addStretch(1)

    return row, switch
