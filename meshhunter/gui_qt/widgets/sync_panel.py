"""SYNC rail block: upload totals, manual batch send, device-contacts clear."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .common import make_button, section_label
from .stat_tile import StatTile


class SyncPanel(QWidget):
    send_wdgwars_clicked = Signal()
    send_ingest_clicked = Signal()
    clear_contacts_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(section_label("SYNC"))

        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(10)
        self.wdgwars_tile = StatTile("WDGWars sent")
        self.ingest_tile = StatTile("Ingest sent")
        tiles_row.addWidget(self.wdgwars_tile)
        tiles_row.addWidget(self.ingest_tile)
        layout.addLayout(tiles_row)

        self.send_wdgwars_button = make_button("Send pending to WDGWars", variant="secondary", height=32)
        self.send_wdgwars_button.clicked.connect(self.send_wdgwars_clicked)
        layout.addWidget(self.send_wdgwars_button)

        self.send_ingest_button = make_button("Send pending to Ingest API", variant="secondary", height=32)
        self.send_ingest_button.clicked.connect(self.send_ingest_clicked)
        layout.addWidget(self.send_ingest_button)

        self.clear_contacts_button = make_button("Clear device contacts", variant="danger", height=32)
        self.clear_contacts_button.setEnabled(False)
        self.clear_contacts_button.clicked.connect(self.clear_contacts_clicked)
        layout.addWidget(self.clear_contacts_button)

    def set_wdgwars_total(self, total):
        self.wdgwars_tile.set_value(total)

    def set_ingest_total(self, total):
        self.ingest_tile.set_value(total)

    def set_clear_contacts_enabled(self, enabled):
        self.clear_contacts_button.setEnabled(enabled)

    def set_send_wdgwars_visible(self, visible):
        self.send_wdgwars_button.setVisible(visible)

    def set_send_ingest_visible(self, visible):
        self.send_ingest_button.setVisible(visible)
