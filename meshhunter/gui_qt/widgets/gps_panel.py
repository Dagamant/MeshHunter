"""GPS settings section: enable switch, serial port picker.

Lives inside SettingsDialog, and only shown at all when the config file
already has a gps_port/gps_enabled key -- see main_window.py's
gps_configured check. The live fix readout isn't configuration, so it's a
main-window status label instead (updated continuously; this panel is only
visible while the dialog is open).
"""

from PySide6.QtWidgets import QComboBox, QVBoxLayout, QWidget

from .common import divider, labeled_switch, section_label


class GPSPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(11)
        layout.addWidget(section_label("GPS"))

        switch_row, self.enabled_switch = labeled_switch("Enabled")
        self.enabled_switch.toggled.connect(self._on_enabled_toggled)
        layout.addWidget(switch_row)

        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumHeight(34)
        layout.addWidget(self.port_combo)

        layout.addWidget(divider())

    def _on_enabled_toggled(self, checked):
        self.port_combo.setEnabled(checked)

    def set_ports(self, values):
        current = self.port_combo.currentText()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItems(values)
        self.port_combo.setCurrentText(current)
        self.port_combo.blockSignals(False)

    def is_enabled(self):
        return self.enabled_switch.isChecked()

    def set_enabled(self, value):
        self.enabled_switch.setChecked(value)
        self.port_combo.setEnabled(value)

    def port(self):
        return self.port_combo.currentText().strip()

    def set_port(self, value):
        self.port_combo.setCurrentText(value)
