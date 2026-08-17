"""ENDPOINTS settings section: WDGWars + ingest ("Wardriver") API config.

Lives inside SettingsDialog. The Wardriver fields are hidden unless the
config file already has an ingest_* key -- see main_window.py's
ingest_configured check -- since they aren't part of example-config.json's
standard fields.
"""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .common import divider, labeled_entry, section_label


class EndpointsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(section_label("ENDPOINTS"))

        api_key_wrap, self.api_key_edit = labeled_entry("WDGWars API key", password=True)
        layout.addWidget(api_key_wrap)

        self._ingest_wrap = QWidget()
        ingest_layout = QVBoxLayout(self._ingest_wrap)
        ingest_layout.setContentsMargins(0, 0, 0, 0)
        ingest_layout.setSpacing(10)
        url_wrap, self.ingest_url_edit = labeled_entry("Wardriver URL")
        ingest_layout.addWidget(url_wrap)
        ingest_key_wrap, self.ingest_api_key_edit = labeled_entry("Wardriver API key", password=True)
        ingest_layout.addWidget(ingest_key_wrap)
        layout.addWidget(self._ingest_wrap)

        layout.addWidget(divider())

    def set_ingest_visible(self, visible):
        self._ingest_wrap.setVisible(visible)

    def api_key(self):
        return self.api_key_edit.text().strip()

    def set_api_key(self, value):
        self.api_key_edit.setText(value)

    def ingest_url(self):
        return self.ingest_url_edit.text().strip()

    def set_ingest_url(self, value):
        self.ingest_url_edit.setText(value)

    def ingest_api_key(self):
        return self.ingest_api_key_edit.text().strip()

    def set_ingest_api_key(self, value):
        self.ingest_api_key_edit.setText(value)
