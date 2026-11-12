"""LORA HAT rail block: pick MeshCore or Meshtastic, its radio preset, and
start/stop listening on the HAT's SX1262 (plus the RTL-SDR waterfall).

Changing a setting while listening emits settings_changed so the main
window can retune; spin boxes are debounced so scrolling through a value
doesn't reconfigure the radio on every step.
"""

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QGridLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from ...core.lora_decode import (
    MESHCORE_BANDWIDTHS,
    MESHCORE_PRESETS,
    MESHTASTIC_PRESETS,
    MESHTASTIC_REGIONS,
    meshtastic_frequency_mhz,
)
from ..theme import DIM, pick_mono_font
from .common import divider, make_button, section_label

_CUSTOM = "Custom"
NETWORKS = ("MeshCore", "Meshtastic")


class HatPanel(QWidget):
    start_clicked = Signal()
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        mono = pick_mono_font()
        self._loading = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(600)
        self._debounce.timeout.connect(self.settings_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(11)
        layout.addWidget(section_label("LORA HAT"))

        self.network_combo = QComboBox()
        self.network_combo.addItems(NETWORKS)
        self.network_combo.setMinimumHeight(34)
        self.network_combo.currentTextChanged.connect(self._on_network_changed)
        layout.addWidget(self.network_combo)

        # -- MeshCore: preset + editable radio params --
        self.meshcore_box = QWidget()
        grid = QGridLayout(self.meshcore_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.mc_preset = QComboBox()
        self.mc_preset.addItems([*MESHCORE_PRESETS, _CUSTOM])
        self.mc_preset.currentTextChanged.connect(self._on_mc_preset_changed)
        self.mc_freq = QDoubleSpinBox()
        self.mc_freq.setRange(860.0, 960.0)
        self.mc_freq.setDecimals(3)
        self.mc_freq.setSingleStep(0.025)
        self.mc_freq.setSuffix(" MHz")
        self.mc_bw = QComboBox()
        self.mc_bw.addItems([f"{bw:g}" for bw in MESHCORE_BANDWIDTHS])
        self.mc_sf = QSpinBox()
        self.mc_sf.setRange(5, 12)
        self.mc_cr = QSpinBox()
        self.mc_cr.setRange(5, 8)
        self.mc_cr.setPrefix("4/")
        for row, (label, widget) in enumerate((
            ("Preset", self.mc_preset), ("Freq", self.mc_freq), ("BW kHz", self.mc_bw),
            ("SF", self.mc_sf), ("CR", self.mc_cr),
        )):
            grid.addWidget(self._field_label(label, mono), row, 0)
            grid.addWidget(widget, row, 1)
        self.mc_freq.valueChanged.connect(self._on_mc_param_changed)
        self.mc_bw.currentTextChanged.connect(self._on_mc_param_changed)
        self.mc_sf.valueChanged.connect(self._on_mc_param_changed)
        self.mc_cr.valueChanged.connect(self._on_mc_param_changed)
        layout.addWidget(self.meshcore_box)

        # -- Meshtastic: region + preset, frequency derived --
        self.meshtastic_box = QWidget()
        grid = QGridLayout(self.meshtastic_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.mt_region = QComboBox()
        self.mt_region.addItems(MESHTASTIC_REGIONS)
        self.mt_preset = QComboBox()
        self.mt_preset.addItems(MESHTASTIC_PRESETS)
        self.mt_info = QLabel()
        self.mt_info.setStyleSheet(f"font-family: '{mono}'; font-size: 11px; color: {DIM};")
        grid.addWidget(self._field_label("Region", mono), 0, 0)
        grid.addWidget(self.mt_region, 0, 1)
        grid.addWidget(self._field_label("Preset", mono), 1, 0)
        grid.addWidget(self.mt_preset, 1, 1)
        grid.addWidget(self.mt_info, 2, 0, 1, 2)
        self.mt_region.currentTextChanged.connect(self._on_mt_changed)
        self.mt_preset.currentTextChanged.connect(self._on_mt_changed)
        layout.addWidget(self.meshtastic_box)

        self.start_button = make_button("Start listening", variant="primary", height=36)
        self.start_button.clicked.connect(self.start_clicked)
        layout.addWidget(self.start_button)

        layout.addWidget(divider())

    @staticmethod
    def _field_label(text, mono):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-family: '{mono}'; font-size: 10px; color: {DIM};")
        return lbl

    # ---- config round-trip --------------------------------------------------

    def set_values(self, config):
        self._loading = True
        try:
            self.network_combo.setCurrentText(config.get("lora_network", "MeshCore"))
            self.mc_preset.setCurrentText(config.get("meshcore_preset", _CUSTOM))
            self.mc_freq.setValue(float(config["meshcore_freq"]))
            self.mc_bw.setCurrentText(f"{float(config['meshcore_bw']):g}")
            self.mc_sf.setValue(int(config["meshcore_sf"]))
            self.mc_cr.setValue(int(config["meshcore_cr"]))
            self.mt_region.setCurrentText(config.get("meshtastic_region", "US"))
            self.mt_preset.setCurrentText(config.get("meshtastic_preset", "LongFast"))
        finally:
            self._loading = False
        self._on_network_changed(self.network_combo.currentText(), emit=False)
        self._update_mt_info()

    def values(self):
        return {
            "lora_network": self.network_combo.currentText(),
            "meshcore_preset": self.mc_preset.currentText(),
            "meshcore_freq": round(self.mc_freq.value(), 4),
            "meshcore_bw": float(self.mc_bw.currentText()),
            "meshcore_sf": self.mc_sf.value(),
            "meshcore_cr": self.mc_cr.value(),
            "meshtastic_region": self.mt_region.currentText(),
            "meshtastic_preset": self.mt_preset.currentText(),
        }

    def set_running(self, running):
        self.start_button.setText("Stop listening" if running else "Start listening")

    # ---- slots ----------------------------------------------------------------

    def _changed(self, debounce=False):
        if self._loading:
            return
        if debounce:
            self._debounce.start()
        else:
            self._debounce.stop()
            self.settings_changed.emit()

    def _on_network_changed(self, network, emit=True):
        self.meshcore_box.setVisible(network == "MeshCore")
        self.meshtastic_box.setVisible(network == "Meshtastic")
        if emit:
            self._changed()

    def _on_mc_preset_changed(self, name):
        if name in MESHCORE_PRESETS and not self._loading:
            freq, bw, sf, cr = MESHCORE_PRESETS[name]
            self._loading = True
            try:
                self.mc_freq.setValue(freq)
                self.mc_bw.setCurrentText(f"{bw:g}")
                self.mc_sf.setValue(sf)
                self.mc_cr.setValue(cr)
            finally:
                self._loading = False
        self._changed()

    def _on_mc_param_changed(self, *_):
        if self._loading:
            return
        current = (round(self.mc_freq.value(), 3), float(self.mc_bw.currentText()), self.mc_sf.value(), self.mc_cr.value())
        match = next((n for n, p in MESHCORE_PRESETS.items() if p == current), _CUSTOM)
        if match != self.mc_preset.currentText():
            self._loading = True
            self.mc_preset.setCurrentText(match)
            self._loading = False
        self._changed(debounce=True)

    def _on_mt_changed(self, *_):
        self._update_mt_info()
        self._changed()

    def _update_mt_info(self):
        preset = self.mt_preset.currentText()
        bw, sf, cr = MESHTASTIC_PRESETS[preset]
        freq = meshtastic_frequency_mhz(self.mt_region.currentText(), preset)
        self.mt_info.setText(f"{freq:.3f} MHz · SF{sf} · BW{bw:g} · CR4/{cr}")
