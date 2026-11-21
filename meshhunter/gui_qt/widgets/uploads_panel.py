"""Settings > Uploads: one card per endpoint (WDGWars, and the WarDriver
ingest API when configured), each with its own auto-upload switch, plus
the two behaviour toggles that used to be the AUTOMATION section.

The WarDriver card is hidden unless the config file already has an
ingest_* key -- see main_window.py's ingest_configured check.
"""

import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QWidget

from ...core.uploads import test_ingest_endpoint
from ..theme import ACCENT, DANGER_TEXT, DIM, LOG_WARN
from .settings_common import Card, SettingsPage, label, link, mono_style, sans_style, toggle_row
from .toggle_switch import ToggleSwitch


def _field(password=False):
    edit = QLineEdit()
    edit.setMinimumHeight(30)
    edit.setStyleSheet(mono_style(12))
    if password:
        edit.setEchoMode(QLineEdit.Password)
    return edit


class _EndpointCard(Card):
    def __init__(self, name):
        super().__init__()
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(label(name, sans_style(13, weight=500)))
        self.status = QLabel()
        head.addWidget(self.status)
        head.addStretch(1)
        head.addWidget(label("Auto-upload", mono_style(10.5, DIM)))
        self.auto_switch = ToggleSwitch()
        head.addWidget(self.auto_switch)
        self.body.addLayout(head)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(8)
        self.grid.setColumnMinimumWidth(0, 70)
        self.grid.setColumnStretch(1, 1)
        self.body.addLayout(self.grid)

    def add_row(self, text, edit, action=None):
        r = self.grid.rowCount()
        self.grid.addWidget(label(text, mono_style(10.5, DIM)), r, 0)
        self.grid.addWidget(edit, r, 1)
        if action is not None:
            self.grid.addWidget(action, r, 2)

    def set_status(self, text, color):
        self.status.setText(f"● {text}")
        self.status.setStyleSheet(mono_style(10.5, color))


def _show_toggle(edit):
    btn = link("Show")

    def flip():
        hidden = edit.echoMode() == QLineEdit.Password
        edit.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
        btn.setText("Hide" if hidden else "Show")

    btn.clicked.connect(flip)
    return btn


class UploadsPanel(SettingsPage):
    TITLE = "Uploads"
    NAV_TITLE = "Uploads"
    DESCRIPTION = "Where logged nodes are sent. Each endpoint has its own auto-upload switch."
    _test_done = Signal(bool, str)

    def __init__(self, ingest_configured=True, sent=None, parent=None):
        super().__init__(parent)
        self._ingest_configured = ingest_configured
        self._sent = sent or {}

        self.wdg_card = _EndpointCard("WDGWars")
        self.api_key_edit = _field(password=True)
        self.wdg_card.add_row("API key", self.api_key_edit, _show_toggle(self.api_key_edit))
        self.body.addWidget(self.wdg_card)

        self.ingest_card = _EndpointCard("WarDriver ingest")
        self.ingest_url_edit = _field()
        self.ingest_url_edit.setPlaceholderText("http://host:port/api/mesh/nodes/ingest")
        test_wrap = QWidget()
        test_wrap.setStyleSheet("background: transparent;")
        test_row = QHBoxLayout(test_wrap)
        test_row.setContentsMargins(0, 0, 0, 0)
        test_row.setSpacing(6)
        self.test_link = link("Test", self._run_test)
        self.test_link.setToolTip("POST an empty node list to the URL (nothing is ingested)")
        self.test_result = QLabel()
        test_row.addWidget(self.test_link)
        test_row.addWidget(self.test_result)
        self.ingest_card.add_row("URL", self.ingest_url_edit, test_wrap)
        self.ingest_api_key_edit = _field(password=True)
        self.ingest_card.add_row("API key", self.ingest_api_key_edit, _show_toggle(self.ingest_api_key_edit))
        self.ingest_card.setVisible(ingest_configured)
        self.body.addWidget(self.ingest_card)

        row, self.auto_clear_contacts_switch = toggle_row(
            "Auto-clear logged nodes from device", "keeps the companion's contact list free")
        self.body.addWidget(row)
        row, self.batch_uploads_switch = toggle_row(
            "Batch uploads", "hold until “Send pending” in the Sync menu")
        self.body.addWidget(row)

        for edit in (self.api_key_edit, self.ingest_url_edit, self.ingest_api_key_edit):
            edit.textChanged.connect(self._on_edit)
        for switch in (self.wdg_card.auto_switch, self.ingest_card.auto_switch,
                       self.auto_clear_contacts_switch, self.batch_uploads_switch):
            switch.toggled.connect(lambda _=None: self.changed.emit())
        self.ingest_url_edit.textChanged.connect(lambda _: self.test_result.setText(""))
        self._test_done.connect(self._on_test_done)

    # ---- values ----------------------------------------------------------------

    def set_values(self, config):
        self.api_key_edit.setText(config.get("api_key", ""))
        self.ingest_url_edit.setText(config.get("ingest_url", ""))
        self.ingest_api_key_edit.setText(config.get("ingest_api_key", ""))
        self.wdg_card.auto_switch.setChecked(config.get("auto_upload", False))
        self.ingest_card.auto_switch.setChecked(config.get("ingest_auto_send", False))
        self.auto_clear_contacts_switch.setChecked(config.get("auto_clear_contacts", False))
        self.batch_uploads_switch.setChecked(config.get("batch_uploads", False))
        self._update_status()

    def values(self):
        out = {
            "api_key": self.api_key_edit.text().strip(),
            "auto_upload": self.wdg_card.auto_switch.isChecked(),
            "auto_clear_contacts": self.auto_clear_contacts_switch.isChecked(),
            "batch_uploads": self.batch_uploads_switch.isChecked(),
        }
        if self._ingest_configured:
            out.update({
                "ingest_url": self.ingest_url_edit.text().strip(),
                "ingest_api_key": self.ingest_api_key_edit.text().strip(),
                "ingest_auto_send": self.ingest_card.auto_switch.isChecked(),
            })
        return out

    def summary(self):
        v = self.values()
        names = []
        if v["api_key"]:
            names.append("WDGWars")
        if self._ingest_configured and v.get("ingest_url"):
            names.append("WarDriver")
        if not names:
            return "Not set up"
        auto = v["auto_upload"] or v.get("ingest_auto_send")
        return " + ".join(names) + (" · batch" if v["batch_uploads"] else " · auto" if auto else " · manual")

    # ---- status ----------------------------------------------------------------

    def _on_edit(self, _text=None):
        self._update_status()
        self.changed.emit()

    def _update_status(self):
        sent = self._sent.get("wdgwars", 0)
        if self.api_key_edit.text().strip():
            self.wdg_card.set_status(f"key set · {sent} sent", ACCENT)
        else:
            self.wdg_card.set_status("no API key", DIM)
        sent = self._sent.get("ingest", 0)
        if not self.ingest_url_edit.text().strip():
            self.ingest_card.set_status("no URL", DIM)
        elif not self.ingest_api_key_edit.text().strip():
            self.ingest_card.set_status(f"no API key · {sent} sent", LOG_WARN)
        else:
            self.ingest_card.set_status(f"{sent} sent", ACCENT)

    def _run_test(self):
        url = self.ingest_url_edit.text().strip()
        key = self.ingest_api_key_edit.text().strip()
        self.test_link.setEnabled(False)
        self.test_result.setStyleSheet(mono_style(10.5, DIM))
        self.test_result.setText("…")
        def work():
            result = test_ingest_endpoint(url, key)
            try:
                self._test_done.emit(*result)
            except RuntimeError:  # dialog closed while the request ran
                pass

        threading.Thread(target=work, name="ingest-test", daemon=True).start()

    def _on_test_done(self, ok, text):
        self.test_link.setEnabled(True)
        self.test_result.setStyleSheet(mono_style(10.5, ACCENT if ok else DANGER_TEXT))
        self.test_result.setText(("✓ " if ok else "✗ ") + text)
        self.test_result.setToolTip(text)
