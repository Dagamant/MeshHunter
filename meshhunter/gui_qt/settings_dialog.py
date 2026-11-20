"""Modal Settings dialog (redesign 1e): a left nav of pages, each with a
live one-line summary, driving a stack of pages -- GPS, Uploads,
Channels, LoRa sniffer -- and a footer with an unsaved-change count and
Cancel/Save.

These are configuration values (edited occasionally), not live status;
device selection/connect lives in the main window's top bar. GPS and the
WarDriver ingest fields are opt-in power-user settings -- not part of
example-config.json's standard set -- so they only show when the config
file already has the corresponding key(s) (see main_window's
gps_configured / ingest_configured).

values(previous) returns the same config keys as before the redesign, so
MainWindow._apply_settings is unchanged.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.paths import CONFIG_PATH
from .theme import ACCENT, DIM, LINE, LOG_PATH, LOG_WARN, PANEL_2, RAIL_TOP, TXT
from .widgets.channels_panel import ChannelsPanel
from .widgets.common import make_button
from .widgets.gps_panel import GPSPanel
from .widgets.settings_common import mono_style, sans_style
from .widgets.sniffer_panel import SnifferPanel
from .widgets.uploads_panel import UploadsPanel

NAV_WIDTH = 220
IDLE_STATUS = "Channel changes apply to the live session."


class _NavItem(QWidget):
    def __init__(self, title):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(2)
        self.title = QLabel(title)
        self.summary = QLabel()
        lay.addWidget(self.title)
        lay.addWidget(self.summary)
        self.set_selected(False)

    def set_selected(self, on):
        self.title.setStyleSheet(sans_style(13, ACCENT if on else TXT, 500))
        self.summary.setStyleSheet(mono_style(10.5, DIM))

    def set_summary(self, text):
        self.summary.setText(text)
        self.summary.setToolTip(text)


class SettingsDialog(QDialog):
    def __init__(self, config_data, ports, gps_configured, ingest_configured, parent=None, *,
                 channel_counts=None, unknown_hashes=0, find_channels=None, sent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(880, 660)
        self.setMinimumSize(720, 520)
        self._gps_configured = gps_configured
        self._ingest_configured = ingest_configured
        self._previous = dict(config_data)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        main = QHBoxLayout()
        main.setSpacing(0)
        outer.addLayout(main, stretch=1)

        # ---- pages
        self.gps_panel = GPSPanel()
        self.gps_panel.set_ports(ports)
        self.gps_panel.set_enabled(config_data.get("gps_enabled", True))
        self.gps_panel.set_port(config_data.get("gps_port", ""))
        self.uploads_panel = UploadsPanel(ingest_configured, sent)
        self.uploads_panel.set_values(config_data)
        self.channels_panel = ChannelsPanel(config_data, channel_counts, unknown_hashes, find_channels)
        self.sniffer_panel = SnifferPanel(config_data)
        self.pages = ([self.gps_panel] if gps_configured else []) + [
            self.uploads_panel, self.channels_panel, self.sniffer_panel]

        # ---- nav
        nav_wrap = QFrame()
        nav_wrap.setObjectName("settingsNav")
        nav_wrap.setFixedWidth(NAV_WIDTH)
        self._nav_wrap = nav_wrap
        nav_wrap.setStyleSheet(f"#settingsNav {{ background: {RAIL_TOP}; border: none;"
                               f" border-right: 1px solid {LINE}; }}")
        nav_lay = QVBoxLayout(nav_wrap)
        nav_lay.setContentsMargins(10, 14, 10, 14)
        nav_lay.setSpacing(8)
        self.nav = QListWidget()
        self.nav.setObjectName("settingsNavList")
        self.nav.setFrameShape(QFrame.NoFrame)
        self.nav.setSpacing(2)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setStyleSheet(
            "#settingsNavList { background: transparent; border: none; outline: none; }"
            " #settingsNavList::item { border: 1px solid transparent; border-radius: 8px; }"
            f" #settingsNavList::item:selected {{ background: {PANEL_2}; border: 1px solid {LINE}; }}"
            f" #settingsNavList::item:hover:!selected {{ background: {PANEL_2}; }}")
        self.stack = QStackedWidget()
        self._nav_items = []
        for page in self.pages:
            item = QListWidgetItem()
            widget = _NavItem(page.NAV_TITLE)
            item.setSizeHint(widget.sizeHint())
            self.nav.addItem(item)
            self.nav.setItemWidget(item, widget)
            self._nav_items.append(widget)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
            page.changed.connect(self._on_changed)
        nav_lay.addWidget(self.nav, stretch=1)
        path = str(CONFIG_PATH).replace(str(Path.home()), "~", 1)
        foot = QLabel(f"{path}\nmode 0600, holds API keys")
        foot.setWordWrap(True)
        foot.setStyleSheet(mono_style(10, LOG_PATH))
        nav_lay.addWidget(foot)
        main.addWidget(nav_wrap)
        main.addWidget(self.stack, stretch=1)
        self.nav.currentRowChanged.connect(self._on_nav)

        # ---- footer
        footer = QFrame()
        footer.setObjectName("settingsFooter")
        footer.setFixedHeight(56)
        footer.setStyleSheet(f"#settingsFooter {{ background: {RAIL_TOP}; border: none;"
                             f" border-top: 1px solid {LINE}; }}")
        row = QHBoxLayout(footer)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(10)
        self.status_label = QLabel(IDLE_STATUS)
        row.addWidget(self.status_label, stretch=1)
        # Plain QDialogButtonBox pulls in the platform's native icon theme
        # (a red stop-sign Cancel, a blue floppy-disk Save on this system),
        # which clashes with every other control in the dialog -- so these
        # go through the same make_button() path as the rest of the app.
        cancel_btn = make_button("Cancel", variant="secondary", height=34)
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)
        save_btn = make_button("Save", variant="primary", height=34)
        save_btn.setAutoDefault(False)
        save_btn.clicked.connect(self.accept)
        row.addWidget(save_btn)
        for btn in (cancel_btn, save_btn):
            btn.setStyleSheet(sans_style(13, weight=500, extra="padding: 0 18px;").replace(f"color: {TXT};", ""))
        outer.addWidget(footer)

        self.nav.setCurrentRow(self.pages.index(self.channels_panel) if not gps_configured else 0)
        self._on_changed()

    def fit_small(self, width, height):
        """Fit a fixed small screen (the pocket build's 640x480 panel)."""
        self.setMinimumSize(0, 0)
        self._nav_wrap.setFixedWidth(min(NAV_WIDTH, width // 4))
        self.resize(width, height)

    # ---- nav / status ----------------------------------------------------------------

    def _on_nav(self, row):
        self.stack.setCurrentIndex(row)
        for i, item in enumerate(self._nav_items):
            item.set_selected(i == row)

    def show_page(self, page):
        if page in self.pages:
            self.nav.setCurrentRow(self.pages.index(page))

    def changed_keys(self):
        merged = self.values(self._previous)
        return [k for k in merged if merged[k] != self._previous.get(k)]

    def _on_changed(self):
        for page, item in zip(self.pages, self._nav_items):
            item.set_summary(page.summary())
        n = len(self.changed_keys())
        if n:
            self.status_label.setText(f"● {n} unsaved change{'' if n == 1 else 's'}")
            self.status_label.setStyleSheet(mono_style(11, LOG_WARN))
        else:
            self.status_label.setText(IDLE_STATUS)
            self.status_label.setStyleSheet(mono_style(11, DIM))

    # ---- values ----------------------------------------------------------------------

    def values(self, previous):
        """A copy of `previous` config with this dialog's fields merged in."""
        merged = dict(previous)
        merged.update(self.uploads_panel.values())
        merged.update(self.channels_panel.values())
        merged.update(self.sniffer_panel.values())
        if self._gps_configured:
            merged.update(self.gps_panel.values())
        return merged
