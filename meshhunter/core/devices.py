"""Finding/selecting a device to connect to: serial ports and BLE scanning."""

import asyncio

import serial.tools.list_ports
from bleak import BleakScanner

from .constants import BLE_PREFIX, BLE_SCAN_TIMEOUT


def list_serial_ports():
    return [p.device for p in serial.tools.list_ports.comports()]


def format_ble_entry(name, address):
    return f"{BLE_PREFIX}{name} ({address})"


def parse_device_selection(value):
    """("ble", address) for a "BLE: name (address)" entry, else ("serial", value)."""
    if value.startswith(BLE_PREFIX) and value.endswith(")") and "(" in value:
        return "ble", value[value.rindex("(") + 1:-1]
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
