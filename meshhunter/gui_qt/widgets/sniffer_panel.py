"""SNIFFER settings section: what the LoRa sniffer listens on, and how it hops.

Lives inside SettingsDialog. Only matters when the connected device is a
MeshHunter sniffer (see core/sniffer_worker.py), but always shown so it can
be set up before plugging one in. Changes apply live to a connected sniffer.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.sniffer_profiles import DEFAULT_FIXED_PROFILE, DEFAULT_HOP_PROFILES, all_profiles
from ..theme import DIM, pick_mono_font
from .common import divider, section_label

_MODES = [("hop", "Hop between profiles"), ("fixed", "Fixed on one profile")]


def _spin(maximum, suffix=" ms", special=None):
    box = QSpinBox()
    box.setRange(0, maximum)
    box.setSingleStep(250)
    box.setSuffix(suffix)
    box.setMinimumHeight(30)
    if special:
        box.setSpecialValueText(special)
    return box


class SnifferPanel(QWidget):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        mono = pick_mono_font()
        self._profiles = all_profiles(config)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(section_label("LORA SNIFFER"))

        self.mode_combo = QComboBox()
        self.mode_combo.setMinimumHeight(32)
        for key, label in _MODES:
            self.mode_combo.addItem(label, key)
        layout.addWidget(self.mode_combo)

        self.fixed_combo = QComboBox()
        self.fixed_combo.setMinimumHeight(32)
        for p in self._profiles:
            self.fixed_combo.addItem(p.label(), p.key)
        layout.addWidget(self.fixed_combo)

        self.hop_list = QListWidget()
        self.hop_list.setMinimumHeight(150)
        for p in self._profiles:
            item = QListWidgetItem(p.label())
            item.setData(Qt.UserRole, p.key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.hop_list.addItem(item)
        layout.addWidget(self.hop_list)

        self.timing_widget = QWidget()
        form = QFormLayout(self.timing_widget)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self.dwell_spin = _spin(5000, special="Auto")
        self.dwell_spin.setSingleStep(10)
        self.linger_spin = _spin(60000)
        self.partial_linger_spin = _spin(60000)
        self.linger_max_spin = _spin(120000)
        form.addRow("Listen per profile", self.dwell_spin)
        form.addRow("Stay after a packet", self.linger_spin)
        form.addRow("Stay after a partial", self.partial_linger_spin)
        form.addRow("Max stay", self.linger_max_spin)
        layout.addWidget(self.timing_widget)

        self.estimate_label = QLabel()
        self.estimate_label.setWordWrap(True)
        self.estimate_label.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
        layout.addWidget(self.estimate_label)

        multi_note = QLabel(
            "Several radios (connect to \u201cAll LoRa sniffers\u201d): the profiles are shared out "
            "between them. Hop: the hop list is split across the radios, and a radio left with "
            "one profile stays on it. Fixed: the first radio takes the fixed profile, each other "
            "radio the next profile in the hop list."
        )
        multi_note.setWordWrap(True)
        multi_note.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
        layout.addWidget(multi_note)

        layout.addWidget(divider())

        self.mode_combo.currentIndexChanged.connect(self._update_visibility)
        self.hop_list.itemChanged.connect(self._update_estimate)
        self.dwell_spin.valueChanged.connect(self._update_estimate)
        self.fixed_combo.currentIndexChanged.connect(self._update_estimate)

        self.set_values(config)

    def _mode(self):
        return self.mode_combo.currentData()

    def _checked_keys(self):
        keys = []
        for i in range(self.hop_list.count()):
            item = self.hop_list.item(i)
            if item.checkState() == Qt.Checked:
                keys.append(item.data(Qt.UserRole))
        return keys

    def _update_visibility(self):
        hop = self._mode() == "hop"
        self.fixed_combo.setVisible(not hop)
        self.hop_list.setVisible(hop)
        self.timing_widget.setVisible(hop)
        self._update_estimate()

    def _update_estimate(self):
        if self._mode() != "hop":
            self.estimate_label.setText(
                "Listens continuously on one profile: hears every packet on it, nothing else."
            )
            return
        by_key = {p.key: p for p in self._profiles}
        chosen = [by_key[k] for k in self._checked_keys() if k in by_key]
        if not chosen:
            self.estimate_label.setText("Tick at least one profile.")
            return
        dwell = self.dwell_spin.value()
        # ~2 ms per retune on top of each dwell.
        cycle = sum((dwell or p.auto_dwell_ms()) + 2 for p in chosen)
        lines = [f"One full hop cycle ≈ {cycle} ms."]
        for p in chosen:
            preamble_ms = 16 * p.symbol_ms()  # both networks transmit 16-symbol preambles
            catch = min(1.0, max(0.0, (preamble_ms - 6 * p.symbol_ms() + (dwell or p.auto_dwell_ms())) / cycle))
            lines.append(f"{p.name}: preamble {preamble_ms:.0f} ms → ~{catch:.0%} of first transmissions caught")
        lines.append("Repeats get caught more often, because it stays on a profile after hearing traffic.")
        self.estimate_label.setText("\n".join(lines))

    def set_values(self, config):
        mode = config.get("sniffer_mode", "hop")
        self.mode_combo.setCurrentIndex(0 if mode == "hop" else 1)
        idx = self.fixed_combo.findData(config.get("sniffer_fixed_profile", DEFAULT_FIXED_PROFILE))
        self.fixed_combo.setCurrentIndex(max(0, idx))
        hop_keys = set(config.get("sniffer_hop_profiles") or DEFAULT_HOP_PROFILES)
        self.hop_list.blockSignals(True)
        for i in range(self.hop_list.count()):
            item = self.hop_list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in hop_keys else Qt.Unchecked)
        self.hop_list.blockSignals(False)
        self.dwell_spin.setValue(int(config.get("sniffer_dwell_ms", 0)))
        self.linger_spin.setValue(int(config.get("sniffer_linger_ms", 3000)))
        self.partial_linger_spin.setValue(int(config.get("sniffer_partial_linger_ms", 2000)))
        self.linger_max_spin.setValue(int(config.get("sniffer_linger_max_ms", 12000)))
        self._update_visibility()

    def values(self):
        return {
            "sniffer_mode": self._mode(),
            "sniffer_fixed_profile": self.fixed_combo.currentData(),
            "sniffer_hop_profiles": self._checked_keys() or list(DEFAULT_HOP_PROFILES),
            "sniffer_dwell_ms": self.dwell_spin.value(),
            "sniffer_linger_ms": self.linger_spin.value(),
            "sniffer_partial_linger_ms": self.partial_linger_spin.value(),
            "sniffer_linger_max_ms": self.linger_max_spin.value(),
        }
