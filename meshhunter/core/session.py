"""Connection/session plumbing shared by every front end (Qt GUI, TUI,
headless): picking the right worker for a device, and the per-connection
plaintext session log."""

from datetime import datetime
from pathlib import Path

from .devices import is_sniffer_port, list_sniffer_devices, parse_device_selection
from .paths import SESSION_LOG_DIR
from .sniffer_worker import SnifferWorker
from .worker import MeshCoreWorker


def device_label(selection):
    kind, target = parse_device_selection(selection)
    if kind == "ble":
        return f"BLE {target}"
    if kind == "usb_radio":
        return f"USB LoRa radio {target}".rstrip()
    if kind == "sniffer_group":
        return "all LoRa sniffers"
    return target


def make_worker(selection, config, ui_queue, gps_state):
    """The worker for a device selection ("/dev/ttyX" or a BLE entry):
    a SnifferWorker for MeshHunter sniffer firmware, otherwise a
    MeshCoreWorker. Not started yet."""
    kind, target = parse_device_selection(selection)
    if kind == "sniffer_group":
        # Resolved at connect time: whatever sniffers are plugged in now.
        return SnifferWorker(list_sniffer_devices(), ui_queue, dict(config), gps_state=gps_state)
    if kind == "usb_radio" or (kind == "serial" and is_sniffer_port(target)):
        return SnifferWorker(selection if kind == "usb_radio" else target, ui_queue, dict(config), gps_state=gps_state)
    return MeshCoreWorker(
        target,
        ui_queue,
        connection_kind=kind,
        api_key=config["api_key"],
        auto_upload=config["auto_upload"],
        ingest_url=config["ingest_url"],
        ingest_api_key=config["ingest_api_key"],
        ingest_auto_send=config["ingest_auto_send"],
        auto_clear_contacts=config["auto_clear_contacts"],
        batch_uploads=config["batch_uploads"],
        gps_state=gps_state,
        meshcore_channels=config.get("meshcore_channels"),
    )


def session_log_name(selection):
    kind, target = parse_device_selection(selection)
    if kind == "ble":
        return f"ble_{target.replace(':', '-')}"
    if kind == "usb_radio":
        return f"usbradio_{target or 'radio'}"
    if kind == "sniffer_group":
        return "sniffers"
    return target


class SessionLog:
    """One timestamped .log file per connection, holding every terminal line."""

    def __init__(self):
        self.file = None
        self.path = None

    def open(self, port):
        """Returns an error string, or None on success."""
        self.close()
        try:
            SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_port = Path(port).name or "device"
            path = SESSION_LOG_DIR / f"{ts}_{safe_port}.log"
            self.file = path.open("w", encoding="utf-8")
            self.path = path
        except OSError as exc:
            self.file = None
            self.path = None
            return f"-- Could not open session log file: {exc} --"
        return None

    def write(self, line):
        if self.file is None:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.file.write(f"[{ts}] {line}\n")
            self.file.flush()
        except OSError:
            self.close()

    def close(self):
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                pass
        self.file = None
        self.path = None
