"""Finger-sized input controls for the pocket (touch) UI, where Qt's own
spin-box arrows and combo-box popups are too small to hit reliably.

Stepper: [-big][-] value [+][+big] with press-and-hold auto-repeat; the
value field stays editable for anyone with a keyboard.
Segmented: a row of mutually exclusive tap buttons.

Both take their colours from the pocket stylesheet via object names
(touchStep, touchValue, touchSegment).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLineEdit, QPushButton, QWidget

TOUCH_HEIGHT = 50


def _touch_button(text, width=None):
    btn = QPushButton(text)
    btn.setObjectName("touchStep")
    btn.setFocusPolicy(Qt.NoFocus)
    btn.setMinimumHeight(TOUCH_HEIGHT)
    if width:
        btn.setFixedWidth(width)
    return btn


class Stepper(QWidget):
    valueChanged = Signal(float)

    def __init__(self, minimum, maximum, step, decimals=0, big_step=None, fmt=None, parent=None):
        super().__init__(parent)
        self.minimum = minimum
        self.maximum = maximum
        self.decimals = decimals
        # fmt renders the value for display (e.g. "4/5"); parsing an edited
        # field only reads the leading number, so it must stay first.
        self._fmt = fmt or (lambda v: f"{v:.{decimals}f}")
        self._value = minimum

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        buttons = []
        if big_step:
            buttons.append((f"−{big_step:g}", -big_step, 0))
        buttons.append(("−", -step, 1))
        buttons.append(("+", step, 2))
        if big_step:
            buttons.append((f"+{big_step:g}", big_step, 3))

        self.field = QLineEdit()
        self.field.setObjectName("touchValue")
        self.field.setAlignment(Qt.AlignCenter)
        self.field.setMinimumHeight(TOUCH_HEIGHT)
        self.field.editingFinished.connect(self._on_edited)

        for text, delta, order in buttons:
            btn = _touch_button(text, width=64 if len(text) > 1 else 54)
            btn.setAutoRepeat(True)
            btn.setAutoRepeatDelay(400)
            btn.setAutoRepeatInterval(70)
            btn.clicked.connect(lambda _=False, d=delta: self.setValue(self._value + d))
            if order == 2:
                row.addWidget(self.field, stretch=1)
            row.addWidget(btn)

    def value(self):
        return self._value

    def setValue(self, value):
        value = round(min(self.maximum, max(self.minimum, value)), self.decimals or 0)
        if self.decimals == 0:
            value = int(value)
        changed = value != self._value
        self._value = value
        self.field.setText(self._fmt(value))
        if changed:
            self.valueChanged.emit(value)

    def _on_edited(self):
        text = self.field.text().strip()
        digits = ""
        for ch in text:
            if ch.isdigit() or (ch == "." and "." not in digits):
                digits += ch
            elif digits:
                break
        try:
            self.setValue(float(digits))
        except ValueError:
            self.setValue(self._value)


class Segmented(QWidget):
    """Exclusive choice between (label, value) options."""

    changed = Signal(object)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._values = []
        for i, (label, value) in enumerate(options):
            btn = QPushButton(label)
            btn.setObjectName("touchSegment")
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setMinimumHeight(TOUCH_HEIGHT)
            self._group.addButton(btn, i)
            self._values.append(value)
            row.addWidget(btn, stretch=1)
        self._group.idClicked.connect(lambda i: self.changed.emit(self._values[i]))

    def value(self):
        checked = self._group.checkedId()
        return self._values[checked] if checked >= 0 else None

    def setValue(self, value):
        """Select the option matching value, or clear the selection if none
        does (e.g. a sync word typed by hand that isn't a quick-pick)."""
        if value in self._values:
            self._group.button(self._values.index(value)).setChecked(True)
        else:
            self._group.setExclusive(False)
            for btn in self._group.buttons():
                btn.setChecked(False)
            self._group.setExclusive(True)
