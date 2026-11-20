"""Finding/selecting a device to connect to: serial ports and BLE scanning."""

import asyncio
import os

import serial.tools.list_ports
from bleak import BleakScanner

from .constants import BLE_PREFIX, BLE_SCAN_TIMEOUT, SNIFFER_GROUP_ENTRY, SNIFFER_USB_PRODUCT, USB_RADIO_PREFIX


def list_serial_ports():
    return [p.device for p in serial.tools.list_ports.comports()]


def list_usb_radios():
    """Device-list entries for CH341-bridged USB LoRa sticks (MeshToad,
    MeshStick...). Empty if pyusb/libusb isn't available."""
    try:
        from .ch341 import find_devices
        found = find_devices()
    except Exception:  # no pyusb, no libusb backend, USB error...
        return []
    return [f"{USB_RADIO_PREFIX}{serial or 'unknown serial'}" for serial, _ in found]


def list_sniffer_devices():
    """Every connected sniffer radio: XIAO sniffer ports, then USB sticks."""
    return sorted(p for p in list_serial_ports() if is_sniffer_port(p)) + sorted(list_usb_radios())


def list_devices():
    """Everything Connect can open, minus BLE (which needs a slow scan).
    With two or more sniffer radios plugged in, "All LoRa sniffers" comes
    first: it runs them together as one device."""
    devices = list_serial_ports() + list_usb_radios()
    count = len(list_sniffer_devices())
    if count >= 2:
        devices.insert(0, f"{SNIFFER_GROUP_ENTRY} ({count} radios)")
    return devices


def is_sniffer_port(port):
    """True if `port` is a MeshHunter sniffer, recognised by the USB product
    string its firmware enumerates with -- no need to open the port and
    probe it (which would reset an ESP32-based companion on the way)."""
    # Resolve symlinks so stable /dev/serial/by-id/... paths work too.
    real = os.path.realpath(port)
    for p in serial.tools.list_ports.comports():
        if p.device == port or os.path.realpath(p.device) == real:
            return SNIFFER_USB_PRODUCT in (p.product or "") or SNIFFER_USB_PRODUCT in (p.description or "")
    return False


def format_ble_entry(name, address):
    return f"{BLE_PREFIX}{name} ({address})"


def parse_device_selection(value):
    """("ble", address) for a "BLE: name (address)" entry, ("usb_radio",
    usb serial or "") for a "USB LoRa: serial" entry, ("sniffer_group", "")
    for "All LoRa sniffers", else ("serial", value)."""
    if value.startswith(BLE_PREFIX) and value.endswith(")") and "(" in value:
        return "ble", value[value.rindex("(") + 1:-1]
    if value.startswith(SNIFFER_GROUP_ENTRY):
        return "sniffer_group", ""
    if value.startswith(USB_RADIO_PREFIX):
        serial = value[len(USB_RADIO_PREFIX):].strip()
        return "usb_radio", "" if serial == "unknown serial" else serial
    return "serial", value


async def scan_ble_devices(timeout=BLE_SCAN_TIMEOUT):
    """MeshCore BLE devices only -- meshcore's own BLEConnection matches on
    the same "local name starts with MeshCore" rule (see ble_cx.py), so
    scanning for anything broader would just fill the list with unrelated
    phones/headphones/etc. that could never be connected to anyway."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    devices = []
    for address, (device, adv) in found.items():
        name = adv.local_name or device.name
        if name and name.startswith("MeshCore"):
            devices.append((name, address))
    devices.sort(key=lambda d: d[0])
    return devices


def scan_ble_devices_sync(ui_queue, timeout=BLE_SCAN_TIMEOUT):
    """Thread target: BLE scanning is async and takes seconds, so it runs on
    its own throwaway event loop off the GUI thread, reporting back through
    the same ui_queue every other background operation in this app uses."""
    try:
        devices = asyncio.run(scan_ble_devices(timeout))
    except Exception as exc:
        ui_queue.put(("log", f"-- BLE scan failed: {exc} --"))
        return
    ui_queue.put(("ble_devices", devices))
