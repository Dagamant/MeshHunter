"""A small "value + caption" tile, e.g. node counts, upload totals."""

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..theme import ACCENT, DIM, pick_mono_font


class StatTile(QWidget):
    def __init__(self, caption, parent=None):
        super().__init__(parent)
        self.setProperty("class", "StatTile")

        mono = pick_mono_font()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(5)

        self.value_label = QLabel("0")
        self.value_label.setStyleSheet(f"font-family: '{mono}'; font-size: 19px; color: {ACCENT};")
        layout.addWidget(self.value_label)

        caption_label = QLabel(caption)
        caption_label.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
        layout.addWidget(caption_label)

    def set_value(self, value):
        self.value_label.setText(str(value))
