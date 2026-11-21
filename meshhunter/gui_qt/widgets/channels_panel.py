"""Settings > Channels: the MeshCore group channels the app decrypts.

Each listed channel is decrypted app-side (see core/channels.py), for both
a LoRa sniffer and a companion's RX log; unticking keeps a channel listed
but pauses it. Public is always on. "#name" channels need only their name;
private ones are added as "Name <key>" and listed separately with the key
masked.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QWidget

from ...core.channels import clean_entries, default_channel_entries, parse_entry_text, parse_key
from ..theme import (
    ACCENT_LIGHT,
    DANGER_HOVER_TEXT,
    DIM,
    LINE,
    LINE_SOFT,
    LOG_PATH,
    TXT,
)
from .common import make_button
from .settings_common import CheckMark, SettingsPage, caption, close_button, hint, label, link, mono_style


def masked_key(key):
    try:
        hexkey = parse_key(key).hex()
    except ValueError:
        return "••••"
    return f"{hexkey[:4]} •••• •••• •••• {hexkey[-4:]}"


class _Cell(QFrame):
    """One channel: [check] name ... count ×  (32 px, radius 7)."""

    def __init__(self, panel, entry, count=0, public=False, private=False):
        super().__init__()
        self.panel = panel
        self.entry = entry
        self.setObjectName("chanCell")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(32)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 6, 0)
        row.setSpacing(8)
        self.check = CheckMark()
        self.check.setChecked(public or entry.get("enabled", True))
        self.check.setEnabled(not public)
        self.check.toggled.connect(self._on_toggled)
        row.addWidget(self.check)
        self.name = QLabel(entry["name"])
        row.addWidget(self.name)
        if private:
            self.key_label = label(masked_key(entry.get("key", "")), mono_style(11, DIM))
            row.addWidget(self.key_label)
        row.addStretch(1)
        if public:
            row.addWidget(label("always on", mono_style(10.5, DIM)))
        else:
            if private:
                self.show_link = link("Show key", self._toggle_key)
                row.addWidget(self.show_link)
            else:
                row.addWidget(label(str(count) if count else "—",
                                    mono_style(11, ACCENT_LIGHT if count else LOG_PATH)))
            row.addWidget(close_button(lambda: panel.remove(entry), f"Remove {entry['name']}"))
        self._restyle()

    def _toggle_key(self):
        shown = self.show_link.text() == "Hide key"
        self.key_label.setText(masked_key(self.entry.get("key", "")) if shown else str(self.entry.get("key", "")))
        self.show_link.setText("Show key" if shown else "Hide key")

    def _on_toggled(self, on):
        self.entry["enabled"] = on
        self._restyle()
        self.panel.changed.emit()

    def _restyle(self):
        on = self.check.isChecked()
        self.setStyleSheet(f"#chanCell {{ background: transparent; border: 1px solid {LINE if on else LINE_SOFT};"
                           f" border-radius: 7px; }}")
        self.name.setStyleSheet(mono_style(12, TXT if on else DIM))


class ChannelsPanel(SettingsPage):
    TITLE = "MeshCore channels"
    NAV_TITLE = "Channels"
    DESCRIPTION = ("Ticked channels are decrypted and shown in Messages, for both sniffer and companion. "
                   "Public is always on.")
    COLUMNS = 3

    def __init__(self, config, counts=None, unknown_hashes=0, find_channels=None, parent=None):
        """counts: {channel name lowercased: messages this session};
        find_channels(entries) -> [names] runs "Find channels" over the
        captured traffic (None hides the link)."""
        super().__init__(parent)
        self._counts = counts or {}
        self._find_channels = find_channels
        self._entries = []

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("#hashtag, or Name <32-hex or base64 key>")
        self.entry.setMinimumHeight(34)
        self.entry.setStyleSheet(mono_style(12))
        self.entry.returnPressed.connect(self._add)
        add_row.addWidget(self.entry, stretch=1)
        add_btn = make_button("Add", variant="primary", height=34)
        add_btn.setMinimumWidth(64)
        add_btn.setAutoDefault(False)
        add_btn.clicked.connect(self._add)
        add_row.addWidget(add_btn)
        self.body.addLayout(add_row)

        self.error_label = label("", mono_style(10.5, DANGER_HOVER_TEXT), wrap=True)
        self.error_label.setVisible(False)
        self.body.addWidget(self.error_label)

        sub = QHBoxLayout()
        sub.setSpacing(8)
        sub.addWidget(caption("HASHTAG · messages this session"))
        sub.addStretch(1)
        sub.addWidget(link("Tick all", self._tick_all))
        sub.addWidget(label("|", mono_style(11, LINE)))
        defaults = link("Add defaults", self._add_defaults)
        defaults.setToolTip(", ".join(e["name"] for e in default_channel_entries()))
        sub.addWidget(defaults)
        self.body.addLayout(sub)

        self.grid_holder = QWidget()
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        for c in range(self.COLUMNS):
            self.grid.setColumnStretch(c, 1)
        self.body.addWidget(self.grid_holder)

        self.private_caption = caption("PRIVATE")
        self.body.addWidget(self.private_caption)
        self.private_holder = QWidget()
        self.private_list = QGridLayout(self.private_holder)
        self.private_list.setContentsMargins(0, 0, 0, 0)
        self.private_list.setSpacing(6)
        self.body.addWidget(self.private_holder)

        if find_channels is not None:
            foot = QHBoxLayout()
            foot.setSpacing(6)
            text = (f"Found {unknown_hashes} unknown channel hash{'es' if unknown_hashes != 1 else ''} in traffic."
                    if unknown_hashes else "Recover #hashtag channels from captured traffic.")
            foot.addWidget(hint(text, wrap=False))
            foot.addWidget(link("Find channels…", self._run_find))
            foot.addStretch(1)
            self.body.addLayout(foot)

        self.set_values(config)

    # ---- list -------------------------------------------------------------------

    def _rebuild(self):
        for layout in (self.grid, self.private_list):
            while layout.count():
                w = layout.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
        public = _Cell(self, {"name": "Public", "enabled": True}, public=True)
        cells = [public] + [_Cell(self, e, self._counts.get(e["name"].lower(), 0))
                            for e in self._entries if not e.get("key")]
        for i, cell in enumerate(cells):
            self.grid.addWidget(cell, i // self.COLUMNS, i % self.COLUMNS)
        private = [e for e in self._entries if e.get("key")]
        for i, e in enumerate(private):
            self.private_list.addWidget(_Cell(self, e, private=True), i, 0)
        self.private_caption.setVisible(bool(private))
        self.private_holder.setVisible(bool(private))

    def _names(self):
        return {e["name"].lower() for e in self._entries}

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
        self._entries.append(entry)
        self.entry.clear()
        self._show_error("")
        self._rebuild()
        self.changed.emit()

    def add_names(self, names):
        have = self._names() | {"public"}
        added = [n for n in names if n.lower() not in have]
        self._entries += [{"name": n, "enabled": True} for n in added]
        if added:
            self._rebuild()
            self.changed.emit()
        return added

    def remove(self, entry):
        self._entries = [e for e in self._entries if e is not entry]
        self._rebuild()
        self.changed.emit()

    def _tick_all(self):
        for e in self._entries:
            e["enabled"] = True
        self._rebuild()
        self.changed.emit()

    def _add_defaults(self):
        self.add_names([e["name"] for e in default_channel_entries()])

    def _run_find(self):
        names = self._find_channels(self.values()["meshcore_channels"]) or []
        added = self.add_names(names)
        if names and not added:
            self._show_error("Those channels are already listed.")

    # ---- values -----------------------------------------------------------------

    def set_values(self, config):
        self._entries = [dict(e) for e in clean_entries(config.get("meshcore_channels") or [])]
        self._rebuild()

    def values(self):
        return {"meshcore_channels": [dict(e) for e in self._entries]}

    def summary(self):
        on = sum(1 for e in self._entries if e.get("enabled", True)) + 1  # + Public
        return f"{on} of {len(self._entries) + 1} decrypting"
