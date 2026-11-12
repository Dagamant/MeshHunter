"""Load/save the app's JSON config file."""

import json
import os

from .constants import DEFAULT_INGEST_URL
from .paths import CONFIG_DIR, CONFIG_PATH

# LoRa HAT (SX1262 + RTL-SDR) settings. Kept separate from the companion
# device fields below since they're edited from their own rail panel and
# only matter on Linux with the HAT fitted.
HAT_DEFAULTS = {
    "mode": "companion",  # "companion" (MeshCore device) or "hat" (LoRa HAT)
    "lora_network": "MeshCore",
    "meshcore_preset": "USA/Canada",
    "meshcore_freq": 910.525,
    "meshcore_bw": 62.5,
    "meshcore_sf": 7,
    "meshcore_cr": 5,
    "meshcore_channels": [],  # extra "#hashtag" channels to decrypt
    "meshtastic_region": "US",
    "meshtastic_preset": "LongFast",
    "sdr_sample_rate": 2_400_000,
    "sdr_gain": None,  # None = tuner AGC, else dB
    # Pocket UI "Custom" option: user-entered radio settings + decoder
    "custom_decode": "Capture",  # "MeshCore", "Meshtastic" or "Capture"
    "custom_freq": 915.0,
    "custom_bw": 125.0,
    "custom_sf": 9,
    "custom_cr": 5,
    "custom_sync": 0x12,
    "custom_channel": "LongFast",  # Meshtastic channel name (hash + default key)
    # Pocket (touch) UI logging checkboxes
    "log_packets": True,  # every packet -> lora_packets.csv
    "log_nodes": True,  # MeshCore adverts -> node CSVs (+ uploads, if enabled)
    "log_gps": True,  # tag packets/nodes with our GPS position
    "log_session": False,  # human-readable session log in logs/
}


def _with_hat_defaults(config, data):
    for key, default in HAT_DEFAULTS.items():
        value = data.get(key, default)
        config[key] = list(value) if isinstance(default, list) else value
    return config


def load_config():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return _with_hat_defaults({
                "serial_device": data.get("serial_device", ""),
                "api_key": data.get("api_key", ""),
                "auto_upload": bool(data.get("auto_upload", False)),
                "ingest_url": data.get("ingest_url", DEFAULT_INGEST_URL),
                "ingest_api_key": data.get("ingest_api_key", ""),
                "ingest_auto_send": bool(data.get("ingest_auto_send", False)),
                "auto_clear_contacts": bool(data.get("auto_clear_contacts", False)),
                "gps_port": data.get("gps_port", ""),
                "gps_enabled": bool(data.get("gps_enabled", True)),
                "batch_uploads": bool(data.get("batch_uploads", False)),
            }, data)
        except (json.JSONDecodeError, OSError):
            pass
    return _with_hat_defaults({
        "serial_device": "",
        "api_key": "",
        "auto_upload": False,
        "ingest_url": DEFAULT_INGEST_URL,
        "ingest_api_key": "",
        "ingest_auto_send": False,
        "auto_clear_contacts": False,
        "gps_port": "",
        "gps_enabled": True,
        "batch_uploads": False,
    }, {})


def load_raw_config():
    """The config file's contents exactly as stored on disk, or {} if
    missing/invalid.

    load_config() always fills every key in with a default, so it can't
    tell "the user explicitly set this" apart from "this key was never in
    the file". The GUI needs that distinction to decide whether to show
    optional sections (GPS, wardriver ingest) that aren't part of
    example-config.json's standard fields.
    """
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(config):
    """Write config.json with 0600 permissions (it holds plaintext API keys).

    Uses os.open with an explicit mode rather than Path.write_text so the
    file is never briefly world-readable between creation and chmod, and
    chmods unconditionally afterward to self-heal any pre-existing file
    that was created before this restriction existed.
    """
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(config, indent=2).encode())
    finally:
        os.close(fd)
    os.chmod(CONFIG_PATH, 0o600)
