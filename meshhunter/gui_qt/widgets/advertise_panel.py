"""ADVERTISE rail block: send a 0-hop or flood advert."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .common import divider, make_button, section_label


class AdvertisePanel(QWidget):
    zero_hop_clicked = Signal()
    flood_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(section_label("ADVERTISE"))

        row = QHBoxLayout()
        self.zero_hop_button = make_button("0-hop", variant="secondary", height=34)
        self.zero_hop_button.setEnabled(False)
        self.zero_hop_button.clicked.connect(self.zero_hop_clicked)
        row.addWidget(self.zero_hop_button)

        self.flood_button = make_button("Flood", variant="secondary", height=34)
        self.flood_button.setEnabled(False)
        self.flood_button.clicked.connect(self.flood_clicked)
        row.addWidget(self.flood_button)
        layout.addLayout(row)

        layout.addWidget(divider())

    def set_enabled(self, enabled):
        self.zero_hop_button.setEnabled(enabled)
        self.flood_button.setEnabled(enabled)
