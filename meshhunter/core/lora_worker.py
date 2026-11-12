"""LoRa HAT receive loop: drives the SX1262 in continuous RX for either
MeshCore or Meshtastic, decodes what it hears, appends every packet to
lora_packets.csv (tagged with our GPS fix), and reports to the GUI.

A real OS thread like GPSWorker -- the radio is polled with blocking
spidev/gpiod calls. Reports via ui_queue.put(("hat_packet", RxEvent)) /
("hat_status", str) / ("hat_error", str).
"""

import csv
import threading
from dataclasses import dataclass
from datetime import datetime

from .lora_decode import (
    MESHCORE_SYNC_WORD,
    MESHTASTIC_PRESETS,
    MESHTASTIC_SYNC_WORD,
    PREAMBLE_LEN,
    DecodedPacket,
    decode_capture,
    decode_meshcore,
    decode_meshtastic,
    meshcore_channels,
    meshtastic_frequency_mhz,
)
from .paths import LORA_PACKETS_CSV_PATH

PACKET_CSV_FIELDS = [
    "time", "network", "freq_mhz", "rssi", "snr", "crc_ok", "kind", "sender",
    "text", "route", "node_lat", "node_lon", "logger_lat", "logger_lon", "raw_hex",
]


@dataclass
class RadioSettings:
    network: str  # how to decode: "MeshCore", "Meshtastic" or "Capture" (raw)
    freq_mhz: float
    bw_khz: float
    sf: int
    cr: int
    sync_word: int
    channel_name: str = ""  # Meshtastic: preset name the channel hash/key derive from
    hashtag_channels: tuple = ()
    custom: bool = False  # user-entered radio settings rather than a preset

    @property
    def title(self):
        name = "Packet Capture" if self.network == "Capture" else self.network
        return f"Custom · {name}" if self.custom else name

    @property
    def label(self):
        return f"{self.title} · {self.freq_mhz:.3f} MHz · SF{self.sf} BW{self.bw_khz:g} CR4/{self.cr}"


def radio_settings_from_config(config):
    if config.get("lora_network") == "Custom":
        return RadioSettings(
            config["custom_decode"],
            float(config["custom_freq"]),
            float(config["custom_bw"]),
            int(config["custom_sf"]),
            int(config["custom_cr"]),
            int(config["custom_sync"]),
            channel_name=config.get("custom_channel") or "LongFast",
            hashtag_channels=tuple(config.get("meshcore_channels") or ()),
            custom=True,
        )
    if config.get("lora_network") == "Meshtastic":
        preset = config["meshtastic_preset"]
        bw, sf, cr = MESHTASTIC_PRESETS[preset]
        return RadioSettings(
            "Meshtastic",
            meshtastic_frequency_mhz(config["meshtastic_region"], preset),
            bw, sf, cr, MESHTASTIC_SYNC_WORD, channel_name=preset,
        )
    return RadioSettings(
        "MeshCore",
        float(config["meshcore_freq"]),
        float(config["meshcore_bw"]),
        int(config["meshcore_sf"]),
        int(config["meshcore_cr"]),
        MESHCORE_SYNC_WORD,
        hashtag_channels=tuple(config.get("meshcore_channels") or ()),
    )


@dataclass
class RxEvent:
    time: datetime
    rssi: float
    snr: float
    crc_ok: bool
    packet: DecodedPacket
    raw: bytes


class LoRaWorker(threading.Thread):
    def __init__(self, settings, ui_queue, gps_state=None, log_csv=True):
        super().__init__(daemon=True)
        self.settings = settings
        self.ui_queue = ui_queue
        self.gps_state = gps_state
        self.log_csv = log_csv
        self._stop_requested = threading.Event()

    def stop(self):
        self._stop_requested.set()

    def run(self):
        try:
            from .sx1262 import SX1262
        except ImportError as exc:
            self.ui_queue.put(("hat_error", f"LoRa HAT support needs spidev + gpiod (Linux only): {exc}"))
            return
        s = self.settings
        radio = SX1262()
        try:
            radio.open()
            radio.configure_lora_rx(int(s.freq_mhz * 1e6), s.sf, s.bw_khz, s.cr, s.sync_word, PREAMBLE_LEN)
        except Exception as exc:
            self.ui_queue.put(("hat_error", f"Could not start SX1262: {exc}"))
            radio.close()
            return

        self.ui_queue.put(("hat_status", f"Listening: {s.label}"))
        channels = meshcore_channels(s.hashtag_channels)
        try:
            while not self._stop_requested.is_set():
                rx = radio.receive(timeout=0.5)
                if rx is None:
                    continue
                if s.network == "Capture":
                    packet = decode_capture(rx.payload, crc_ok=not rx.crc_error)
                elif rx.crc_error:
                    packet = DecodedPacket(s.network, "CRC ERROR", text=f"{len(rx.payload)} B")
                elif s.network == "Meshtastic":
                    packet = decode_meshtastic(rx.payload, s.channel_name)
                else:
                    packet = decode_meshcore(rx.payload, channels)
                event = RxEvent(datetime.now(), rx.rssi, rx.snr, not rx.crc_error, packet, rx.payload)
                if self.log_csv:
                    self._append_csv(event)
                self.ui_queue.put(("hat_packet", event))
        except Exception as exc:
            self.ui_queue.put(("hat_error", f"LoRa receive failed: {exc}"))
        finally:
            radio.close()
        self.ui_queue.put(("hat_status", "Stopped"))

    def _append_csv(self, ev):
        fix = self.gps_state.fix if self.gps_state else None
        p = ev.packet
        row = {
            "time": ev.time.strftime("%Y-%m-%d %H:%M:%S"),
            "network": p.network,
            "freq_mhz": f"{self.settings.freq_mhz:.3f}",
            "rssi": ev.rssi,
            "snr": ev.snr,
            "crc_ok": "yes" if ev.crc_ok else "no",
            "kind": p.kind,
            "sender": p.sender,
            "text": p.text,
            "route": p.hops,
            "node_lat": p.lat if p.lat is not None else "",
            "node_lon": p.lon if p.lon is not None else "",
            "logger_lat": fix[0] if fix else "",
            "logger_lon": fix[1] if fix else "",
            "raw_hex": ev.raw.hex(),
        }
        try:
            LORA_PACKETS_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
            new_file = not LORA_PACKETS_CSV_PATH.exists()
            with LORA_PACKETS_CSV_PATH.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=PACKET_CSV_FIELDS)
                if new_file:
                    writer.writeheader()
                writer.writerow(row)
        except OSError as exc:
            self.ui_queue.put(("log", f"-- Could not write {LORA_PACKETS_CSV_PATH}: {exc} --"))
