"""MESHCORE CHANNELS settings section: the group channels the app listens to.

Lives inside SettingsDialog. Each listed channel is decrypted app-side
(see core/channels.py), for both a LoRa sniffer and a companion's RX log;
ticking/unticking keeps a channel listed but pauses it. Public is always
on. "#name" channels need only their name; others need their key.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.channels import clean_entries, default_channel_entries, parse_entry_text
from ..theme import DANGER_HOVER_TEXT, DIM, pick_mono_font
from .common import divider, make_button, section_label


class ChannelsPanel(QWidget):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        mono = pick_mono_font()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(section_label("MESHCORE CHANNELS"))

        note = QLabel(
            "Ticked channels are decrypted and shown in Messages (sniffer and companion). "
            "Public is always on. Add a #hashtag channel by name, or a private one as "
            "“Name <32-hex or base64 key>”."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
        layout.addWidget(note)

        self.list = QListWidget()
        self.list.setMinimumHeight(130)
        self.list.setStyleSheet(f"font-family: '{mono}'; font-size: 12px;")
        layout.addWidget(self.list)

        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("#channel  or  Name <key>")
        self.entry.setMinimumHeight(30)
        self.entry.setStyleSheet(f"font-family: '{mono}'; font-size: 12px;")
        self.entry.returnPressed.connect(self._add)
        add_row.addWidget(self.entry, stretch=1)
        add_btn = make_button("Add", variant="secondary", height=30)
        add_btn.clicked.connect(self._add)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DANGER_HOVER_TEXT};")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        remove_btn = make_button("Remove selected", variant="secondary", height=28)
        remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(remove_btn)
        defaults_btn = make_button("Add defaults", variant="secondary", height=28)
        defaults_btn.setToolTip(", ".join(e["name"] for e in default_channel_entries()))
        defaults_btn.clicked.connect(self._add_defaults)
        btn_row.addWidget(defaults_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        layout.addWidget(divider())
        self.set_values(config)

    def _names(self):
        return {self.list.item(i).data(Qt.UserRole)["name"].lower() for i in range(self.list.count())}

    def _append(self, entry):
        item = QListWidgetItem(entry["name"] + ("  (private key)" if entry.get("key") else ""))
        item.setData(Qt.UserRole, dict(entry))
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if entry.get("enabled", True) else Qt.Unchecked)
        self.list.addItem(item)

    def _show_error(self, text):
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))

    def _add(self):
        try:
            entry = parse_entry_text(self.entry.text())
        except ValueError as exc:
            self._show_error(str(exc))
            return
        if entry["name"].lower() in self._names() | {"public"}:
            self._show_error(f"{entry['name']} is already listed")
            return
        self._append(entry)
        self.entry.clear()
        self._show_error("")

    def _remove_selected(self):
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def _add_defaults(self):
        names = self._names()
        for entry in default_channel_entries():
            if entry["name"].lower() not in names:
                self._append(entry)

    def set_values(self, config):
        self.list.clear()
        for entry in clean_entries(config.get("meshcore_channels") or []):
            self._append(entry)

    def values(self):
        entries = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            entry = dict(item.data(Qt.UserRole))
            entry["enabled"] = item.checkState() == Qt.Checked
            entries.append(entry)
        return {"meshcore_channels": entries}
