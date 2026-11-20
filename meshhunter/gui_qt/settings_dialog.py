"""Modal Settings dialog: GPS, endpoint, automation, MeshCore channel
subscriptions and LoRa sniffer config.

These are configuration values (edited occasionally), not live status, so a
modal dialog opened from the rail's settings button keeps the main window
focused on connection state and live data. Device selection/connect lives
on the rail itself instead (see ConnectionPanel in main_window.py) since
it's a frequent action, not configuration. GPS and Wardriver-ingest
sections are opt-in power-user fields -- not part of example-config.json's
standard set -- so main_window only shows them here when the config file
already has the corresponding key(s) (see its gps_configured /
ingest_configured).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QScrollArea, QVBoxLayout, QWidget

from .widgets.automation_panel import AutomationPanel
from .widgets.channels_panel import ChannelsPanel
from .widgets.endpoints_panel import EndpointsPanel
from .widgets.gps_panel import GPSPanel
from .widgets.sniffer_panel import SnifferPanel


class SettingsDialog(QDialog):
    def __init__(self, config_data, ports, gps_configured, ingest_configured, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        self._gps_configured = gps_configured
        self._ingest_configured = ingest_configured

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        outer.setSpacing(0)

        # Sections scroll as one column: with the sniffer section added the
        # dialog no longer fits the pocket build's fixed 640x480 panel.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        self.gps_panel = GPSPanel()
        self.gps_panel.setVisible(gps_configured)
        layout.addWidget(self.gps_panel)

        self.endpoints_panel = EndpointsPanel()
        self.endpoints_panel.set_ingest_visible(ingest_configured)
        layout.addWidget(self.endpoints_panel)

        self.automation_panel = AutomationPanel()
        self.automation_panel.set_ingest_visible(ingest_configured)
        layout.addWidget(self.automation_panel)

        self.channels_panel = ChannelsPanel(config_data)
        layout.addWidget(self.channels_panel)

        self.sniffer_panel = SnifferPanel(config_data)
        layout.addWidget(self.sniffer_panel)
        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.gps_panel.set_ports(ports)
        self.gps_panel.set_enabled(config_data.get("gps_enabled", True))
        self.gps_panel.set_port(config_data.get("gps_port", ""))

        self.endpoints_panel.set_api_key(config_data.get("api_key", ""))
        self.endpoints_panel.set_ingest_url(config_data.get("ingest_url", ""))
        self.endpoints_panel.set_ingest_api_key(config_data.get("ingest_api_key", ""))

        self.automation_panel.set_values(config_data)

    def values(self, previous):
        """A copy of `previous` config with this dialog's fields merged in."""
        automation = self.automation_panel.values()
        merged = dict(previous)
        merged.update(
            {
                "api_key": self.endpoints_panel.api_key(),
                "auto_upload": automation["auto_upload"],
                "auto_clear_contacts": automation["auto_clear_contacts"],
                "batch_uploads": automation["batch_uploads"],
                **self.channels_panel.values(),
                **self.sniffer_panel.values(),
            }
        )
        if self._gps_configured:
            merged["gps_port"] = self.gps_panel.port()
            merged["gps_enabled"] = self.gps_panel.is_enabled()
        if self._ingest_configured:
            merged["ingest_url"] = self.endpoints_panel.ingest_url()
            merged["ingest_api_key"] = self.endpoints_panel.ingest_api_key()
            merged["ingest_auto_send"] = automation["ingest_auto_send"]
        return merged
