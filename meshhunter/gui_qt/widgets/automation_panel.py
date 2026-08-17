"""AUTOMATION settings section: behavior toggles.

Lives inside SettingsDialog. The "Auto-upload to WarDriver" row is hidden
unless the config file already has an ingest_* key -- see
main_window.py's ingest_configured check -- since it's meaningless
without a configured ingest endpoint and isn't a standard/example-config
field.
"""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .common import divider, labeled_switch, section_label


class AutomationPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)
        layout.addWidget(section_label("AUTOMATION"))

        row, self.auto_upload_switch = labeled_switch("Auto-upload to WDGWars")
        layout.addWidget(row)

        self.ingest_auto_send_row, self.ingest_auto_send_switch = labeled_switch("Auto-upload to WarDriver")
        layout.addWidget(self.ingest_auto_send_row)

        row, self.auto_clear_contacts_switch = labeled_switch("Auto-clear logged nodes")
        layout.addWidget(row)

        row, self.batch_uploads_switch = labeled_switch("Batch uploads (manual send)")
        layout.addWidget(row)

        layout.addWidget(divider())

    def set_ingest_visible(self, visible):
        self.ingest_auto_send_row.setVisible(visible)

    def values(self):
        return {
            "auto_upload": self.auto_upload_switch.isChecked(),
            "ingest_auto_send": self.ingest_auto_send_switch.isChecked(),
            "auto_clear_contacts": self.auto_clear_contacts_switch.isChecked(),
            "batch_uploads": self.batch_uploads_switch.isChecked(),
        }

    def set_values(self, config):
        self.auto_upload_switch.setChecked(config.get("auto_upload", False))
        self.ingest_auto_send_switch.setChecked(config.get("ingest_auto_send", False))
        self.auto_clear_contacts_switch.setChecked(config.get("auto_clear_contacts", False))
        self.batch_uploads_switch.setChecked(config.get("batch_uploads", False))
