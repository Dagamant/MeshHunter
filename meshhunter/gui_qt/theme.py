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
DANGER_TEXT = "#a97b7b"
DANGER_BORDER = "#3d2626"
DANGER_HOVER_BORDER = "#ff7878"
DANGER_HOVER_TEXT = "#e59595"
TERMINAL_DIVIDER = "#1c3a22"
SCROLLBAR_HOVER = "#2f6a3a"
LIVE_PILL_BG = "#0e1f11"
SWITCH_TRACK_ON = "#15341c"
SWITCH_TRACK_OFF = "#141a14"
SWITCH_KNOB_OFF = "#43554a"

RAIL_WIDTH = 380

MONO_FONT_CANDIDATES = (
    "JetBrains Mono", "Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono", "Consolas", "Courier New",
)


def pick_mono_font():
    families = set(QFontDatabase.families())
    for name in MONO_FONT_CANDIDATES:
        if name in families:
            return name
    # Qt's own fixed-font fallback -- always resolves to something
    # monospace, unlike leaving the family unset.
    return QFontDatabase.systemFont(QFontDatabase.FixedFont).family()


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

QMainWindow, #rail, #railContent, #terminalPanel, #nodePanel {{
    background-color: {INK};
}}

#rail {{
    border-right: 1px solid {LINE};
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

QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
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
"""
