"""Color tokens, QSS, and font selection for the Qt GUI.

Buttons are styled via a "variant" dynamic property selected in QSS
(button.setProperty("variant", "primary")), and monospace font selection
goes through QFontDatabase.
"""

from pathlib import Path

from PySide6.QtGui import QFontDatabase

_ASSETS_DIR = Path(__file__).parent / "assets"
# QSS url() wants forward slashes even on Windows.
_CHEVRON_DOWN = (_ASSETS_DIR / "chevron_down.svg").as_posix()
LOGO_PATH = _ASSETS_DIR / "logo.png"

# Design tokens -- matches the MeshHunter logo (neon green / gunmetal
# silver on black). See design_handoff_meshcore_cli/README.md for the
# original cyan palette this replaced.
ACCENT = "#39ff14"
ACCENT_LIGHT = "#9dff85"
ACCENT_DIM = "#1f7a0e"
# Pure black -- matches the logo's own background exactly, so the rail
# (which the logo sits in, see #railContent) doesn't show it as a
# lighter square against the surrounding chrome.
INK = "#000000"
PANEL = "#0d100d"
PANEL_2 = "#111511"
TERMINAL_BG = "#050705"
RAIL_TOP = "#0b0f0b"
TXT = "#dfe6df"
DIM = "#6f8a71"
LINE = "#1d3b23"
LINE_SOFT = "#12261a"
LOG_PATH = "#5a7a5e"
LOG_OK = "#9be89a"
LOG_WARN = "#c9b06a"
DANGER_TEXT = "#d99a9a"
DANGER_BORDER = "#6b2f2f"
DANGER_HOVER_BORDER = "#ff7878"
DANGER_HOVER_TEXT = "#e59595"
TERMINAL_DIVIDER = "#1c3a22"
SCROLLBAR_HOVER = "#2f6a3a"
LIVE_PILL_BG = "#0e1f11"
SWITCH_TRACK_ON = "#15341c"
SWITCH_TRACK_OFF = "#141a14"
# Messages panel: which mesh a packet came from. Kept clear of the app's
# own green so a badge never reads as "status OK".
MESH_MESHCORE = "#5ec8ff"
MESH_MESHTASTIC = "#c79bff"
SWITCH_KNOB_OFF = "#43554a"
# Added with the 2026 desktop redesign (top bar + inspector layout).
ROW_SELECTED = "#0f2414"
ROW_HOVER = "#0b170e"
ROW_SEPARATOR = "#0a120c"
TEXT_SECONDARY = "#8fa592"  # descriptions in Settings
CHECK_OFF_BORDER = "#2a3a2e"
PROFILE_ROW_ON = "#0b130d"
SIGNAL_TRACK = "#0d1a10"

MONO_FONT_CANDIDATES = (
    "JetBrains Mono", "Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono", "Consolas", "Courier New",
)
SANS_FONT_CANDIDATES = ("IBM Plex Sans", "Inter", "Noto Sans", "Segoe UI", "Cantarell", "DejaVu Sans")


def pick_mono_font():
    families = set(QFontDatabase.families())
    for name in MONO_FONT_CANDIDATES:
        if name in families:
            return name
    # Qt's own fixed-font fallback -- always resolves to something
    # monospace, unlike leaving the family unset.
    return QFontDatabase.systemFont(QFontDatabase.FixedFont).family()


def pick_sans_font():
    """Buttons, Settings titles and nav: IBM Plex Sans if installed, else
    the system UI font."""
    families = set(QFontDatabase.families())
    for name in SANS_FONT_CANDIDATES:
        if name in families:
            return name
    return QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()


# QPushButton variants are selected via a "variant" dynamic property
# (button.setProperty("variant", "primary")) rather than a Python kwarg,
# since QSS can only select on properties/classes. Qt handles :hover
# natively -- no manual Enter/Leave binding needed to get a hover-highlight
# border.
STYLESHEET = f"""
QWidget {{
    background-color: {INK};
    color: {TXT};
}}

QMainWindow, #terminalPanel, #nodePanel {{
    background-color: {INK};
}}

/* ---- desktop top bar ---------------------------------------------- */
#topBar {{
    background-color: {RAIL_TOP};
    border-bottom: 1px solid {LINE};
}}
#topBar QWidget {{
    background: transparent;
}}
QFrame[class="VDivider"] {{
    background-color: {LINE};
    border: none;
}}
QToolButton#deviceChip, QToolButton#radioChip {{
    background-color: {PANEL_2};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 0 10px;
}}
QToolButton#deviceChip:hover, QToolButton#sentButton:hover, QToolButton#radioChip:hover {{
    border: 1px solid {ACCENT};
}}
QToolButton#deviceChip::menu-indicator, QToolButton#sentButton::menu-indicator,
QToolButton#radioChip::menu-indicator {{
    image: none;
    width: 0;
}}
QToolButton#sentButton {{
    background-color: transparent;
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 0 10px;
}}
QWidget#segmented {{
    background: transparent;
    border: 1px solid {LINE};
    border-radius: 8px;
}}
QWidget#segmented QPushButton {{
    background: transparent;
    border: none;
    border-radius: 0;
    color: {TXT};
    padding: 0 12px;
}}
QWidget#segmented QPushButton:hover {{
    color: {ACCENT};
}}
QWidget#segmented QPushButton:disabled {{
    color: {DIM};
}}
QWidget#segmented QPushButton:checked {{
    background-color: {SWITCH_TRACK_ON};
    color: {ACCENT_LIGHT};
}}
QWidget#segmented QFrame {{
    background-color: {LINE};
    border: none;
}}
QMenu {{
    background-color: {PANEL_2};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 4px;
    color: {TXT};
}}
QMenu::item {{
    padding: 6px 18px 6px 12px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background-color: {SWITCH_TRACK_ON};
    color: {ACCENT_LIGHT};
}}
QMenu::item:disabled {{
    color: {DIM};
}}
QMenu::separator {{
    height: 1px;
    background: {LINE_SOFT};
    margin: 4px 6px;
}}
QMenu::section {{
    color: {DIM};
    padding: 4px 12px 2px 12px;
}}

/* ---- stats strip / drawers / inspector ---------------------------- */
#statsStrip {{
    background-color: {TERMINAL_BG};
    border-bottom: 1px solid {LINE_SOFT};
}}
#statsStrip QWidget {{
    background: transparent;
}}
#serialBar {{
    background-color: {RAIL_TOP};
    border-top: 1px solid {LINE};
}}
#serialBar QWidget {{
    background: transparent;
}}
#packetInspector {{
    background-color: {PANEL};
    border-left: 1px solid {LINE};
}}
#packetInspector QWidget#inspectorBody, #packetInspector QScrollArea,
#packetInspector QScrollArea > QWidget > QWidget {{
    background-color: {PANEL};
}}
#inspectorHeader {{
    background-color: {RAIL_TOP};
    border-bottom: 1px solid {LINE_SOFT};
}}
QLabel[class="Box"] {{
    background-color: {TERMINAL_BG};
    border: 1px solid {LINE_SOFT};
    border-radius: 8px;
}}
QLabel[class="PathChip"] {{
    background-color: {PANEL_2};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 2px 5px;
}}
QLabel[class="PathChip"][last="true"] {{
    border: 1px solid {ACCENT_DIM};
    color: {ACCENT_LIGHT};
}}
QToolButton[class="Link"] {{
    background: transparent;
    border: none;
    color: {ACCENT_LIGHT};
    padding: 0;
}}
QToolButton[class="Link"]:hover {{
    color: {ACCENT};
}}
QToolButton[class="Close"] {{
    background: transparent;
    border: none;
    color: {DIM};
    font-size: 15px;
    padding: 0 4px;
}}
QToolButton[class="Close"]:hover {{
    color: {ACCENT};
}}
#rfBand {{
    background-color: {TERMINAL_BG};
    border-top: 1px solid {LINE};
}}

#terminalPanel {{
    background-color: {TERMINAL_BG};
}}

#terminalHeader {{
    background-color: {RAIL_TOP};
}}

#nodePanel {{
    background-color: {PANEL};
    border-top: 1px solid {LINE};
}}

QLabel {{
    background: transparent;
}}

QLabel[class="SectionLabel"] {{
    color: {DIM};
}}

QLabel[class="FieldLabel"] {{
    color: {DIM};
}}

QLineEdit, QComboBox {{
    background-color: {PANEL_2};
    border: 1px solid {LINE_SOFT};
    border-radius: 8px;
    padding: 6px 8px;
    color: {TXT};
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {ACCENT};
}}

QSpinBox {{
    background-color: {PANEL_2};
    border: 1px solid {LINE_SOFT};
    border-radius: 8px;
    padding: 4px 8px;
    color: {TXT};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid {LINE_SOFT};
}}

QComboBox::down-arrow {{
    image: url({_CHEVRON_DOWN});
    width: 10px;
    height: 6px;
    margin-right: 6px;
}}

QComboBox QAbstractItemView {{
    background-color: {PANEL_2};
    color: {TXT};
    selection-background-color: {LINE_SOFT};
    border: 1px solid {LINE};
}}

QPushButton {{
    border-radius: 8px;
    padding: 6px 12px;
    font-weight: 500;
}}

QPushButton[variant="primary"] {{
    background-color: #0d2226;
    border: 1px solid {ACCENT};
    color: {ACCENT_LIGHT};
}}
QPushButton[variant="primary"]:hover {{
    background-color: #123037;
}}

QPushButton[variant="secondary"] {{
    background-color: transparent;
    border: 1px solid {LINE};
    color: {TXT};
}}
QPushButton[variant="secondary"]:hover {{
    border: 1px solid {ACCENT};
    color: {ACCENT};
}}

QPushButton[variant="danger"] {{
    background-color: transparent;
    border: 1px solid {DANGER_BORDER};
    color: {DANGER_TEXT};
}}
QPushButton[variant="danger"]:hover {{
    border: 1px solid {DANGER_HOVER_BORDER};
    color: {DANGER_HOVER_TEXT};
}}

QPushButton:disabled {{
    color: {DIM};
    border-color: {LINE_SOFT};
}}

QWidget[class="StatTile"] {{
    background-color: {PANEL_2};
    border: 1px solid {LINE_SOFT};
    border-radius: 8px;
}}

QScrollArea {{
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: {LINE};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {SCROLLBAR_HOVER};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QPlainTextEdit#terminal {{
    background-color: {TERMINAL_BG};
    color: {TXT};
    border: none;
    padding: 14px 16px;
}}

QSplitter::handle {{
    background-color: {LINE_SOFT};
}}

QSplitter::handle:hover {{
    background-color: {SCROLLBAR_HOVER};
}}

QTableView#messages {{
    background-color: {TERMINAL_BG};
    color: {TXT};
    border: none;
    gridline-color: {LINE_SOFT};
    selection-background-color: {LINE_SOFT};
    selection-color: {TXT};
}}

QTableView#messages QHeaderView::section {{
    background-color: {PANEL};
    color: {DIM};
    border: none;
    border-bottom: 1px solid {LINE_SOFT};
    padding: 4px 6px;
}}

/* Desktop message list: painted rows (MessageRowDelegate draws the
   separator, hover and selection), so the view itself stays flat. */
QTableView#messageList, QTableView#sensorList {{
    background-color: {TERMINAL_BG};
    color: {TXT};
    border: none;
    outline: 0;
    selection-background-color: {ROW_SELECTED};
    selection-color: {TXT};
}}
QTableView#messageList QHeaderView::section, QTableView#sensorList QHeaderView::section {{
    background-color: {PANEL};
    color: {DIM};
    border: none;
    border-bottom: 1px solid {LINE_SOFT};
    padding: 0 4px;
    height: 24px;
}}
#messageHeader {{
    background-color: {RAIL_TOP};
    border-bottom: 1px solid {LINE_SOFT};
}}
#messageHeader QLineEdit, #messageHeader QComboBox {{
    border-radius: 7px;
    padding: 2px 8px;
}}
#messageHeader QPushButton {{
    border-radius: 7px;
    padding: 0 10px;
}}

/* Used by PocketMainWindow's tabbed layout (no QTabWidget in the desktop
   window), sized for touch rather than Qt's cursor-sized default tabs. */
QTabWidget::pane {{
    border-top: 1px solid {LINE};
    background-color: {INK};
}}

QTabBar::tab {{
    background-color: {PANEL};
    color: {DIM};
    padding: 10px 18px;
    border: 1px solid {LINE_SOFT};
    border-bottom: none;
}}

QTabBar::tab:selected {{
    background-color: {INK};
    color: {ACCENT};
    border-color: {LINE};
}}

QTabBar::tab:!selected:hover {{
    color: {TXT};
}}
"""
