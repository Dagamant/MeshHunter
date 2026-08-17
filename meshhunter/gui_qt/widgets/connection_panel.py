"""CONNECTION rail block: device picker + the Connect/Disconnect action.

Lives directly on the main window rail, right under the brand/live-status
header, rather than in the Settings dialog -- picking a device and
connecting to it is a frequent action, not an occasional configuration
value.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QVBoxLayout, QWidget

from .common import divider, make_button, section_label


class ConnectionPanel(QWidget):
    refresh_clicked = Signal()
    connect_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(11)
        layout.addWidget(section_label("CONNECTION"))

        row1 = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.setEditable(True)
        self.device_combo.setMinimumHeight(34)
        row1.addWidget(self.device_combo, stretch=1)
        refresh_btn = make_button("↻", variant="secondary", height=34)
        refresh_btn.setFixedWidth(34)
        refresh_btn.clicked.connect(self.refresh_clicked)
        row1.addWidget(refresh_btn)
        layout.addLayout(row1)

        self.connect_button = make_button("Connect", variant="primary", height=36)
        self.connect_button.clicked.connect(self.connect_clicked)
        layout.addWidget(self.connect_button)

        layout.addWidget(divider())

    def set_devices(self, values):
        current = self.device_combo.currentText()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItems(values)
        self.device_combo.setCurrentText(current)
        self.device_combo.blockSignals(False)

    def device_selection(self):
        return self.device_combo.currentText().strip()

    def set_device_selection(self, value):
        self.device_combo.setCurrentText(value)

    def set_connect_text(self, text):
        self.connect_button.setText(text)
