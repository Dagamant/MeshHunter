"""Settings > GPS: enable switch and serial port picker.

Only shown when the config file already has a gps_port/gps_enabled key --
see main_window.py's gps_configured check. The live fix readout isn't
configuration, so it lives in the main window's top bar instead.
"""

from PySide6.QtWidgets import QComboBox

from .settings_common import SettingsPage, caption, hint, toggle_row


class GPSPanel(SettingsPage):
    TITLE = "GPS"
    NAV_TITLE = "GPS"
    DESCRIPTION = "Positions are stamped onto logged nodes and uploads. Use the GPS receiver's serial port."

    def __init__(self, parent=None):
        super().__init__(parent)
        row, self.enabled_switch = toggle_row("Enabled")
        self.enabled_switch.toggled.connect(self._on_enabled_toggled)
        self.body.addWidget(row)

        self.body.addWidget(caption("PORT"))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumHeight(34)
        self.port_combo.setMaximumWidth(360)
        self.port_combo.currentTextChanged.connect(lambda _: self.changed.emit())
        self.body.addWidget(self.port_combo)
        self.body.addWidget(hint("A GPS puck is usually /dev/ttyACM0 or /dev/ttyUSB0. "
                                 "Don't pick the radio's own port."))

    def _on_enabled_toggled(self, checked):
        self.port_combo.setEnabled(checked)
        self.changed.emit()

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

    def summary(self):
        if not self.is_enabled():
            return "Off"
        return f"On · {self.port() or 'no port'}"

    def values(self):
        return {"gps_port": self.port(), "gps_enabled": self.is_enabled()}
