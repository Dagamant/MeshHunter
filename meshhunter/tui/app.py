"""Entry point for the terminal front ends.

  meshhunter-tui              full-screen curses interface (main.py --tui)
  meshhunter-tui --headless   no interface: log lines to stdout, for systemd/cron

Neither needs PySide6 (see requirements-cli.txt), so both run on boards
where Qt isn't available. The same config.json, CSVs, uploads and
session logs as the desktop app are used.
"""

import argparse
import asyncio
import os
import signal
import sys
import time

from ..core import ble_compat
from ..core.paths import CONFIG_PATH, DATA_DIR, migrate_legacy_data
from .controller import Controller


def parse_args(argv):
    parser = argparse.ArgumentParser(description="MeshHunter for the terminal")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tui", action="store_true", help="full-screen terminal interface (default)")
    mode.add_argument("--headless", action="store_true",
                      help="no interface: connect, log to stdout, reconnect forever")
    parser.add_argument("--device", "-d", help="serial port or BLE entry to use (saved to config)")
    parser.add_argument("--connect", action="store_true",
                        help="TUI: connect on startup (implied by --device and by --headless)")
    parser.add_argument("--no-reconnect", action="store_true",
                        help="don't retry when the connection fails or drops")
    parser.add_argument("--list-devices", action="store_true", help="print serial ports and exit")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.list_devices:
        from ..core.constants import SNIFFER_GROUP_ENTRY
        from ..core.devices import is_sniffer_port, list_serial_ports, list_sniffer_devices, list_usb_radios
        for port in list_serial_ports():
            print(port + ("   (MeshHunter sniffer)" if is_sniffer_port(port) else ""))
        for entry in list_usb_radios():
            print(f'"{entry}"   (USB LoRa stick, e.g. MeshToad; quote it for --device)')
        sniffers = list_sniffer_devices()
        if len(sniffers) >= 2:
            print(f'"{SNIFFER_GROUP_ENTRY}"   (all {len(sniffers)} sniffer radios together)')
        return 0
    ble_compat.apply_ble_workarounds()
    migrated = migrate_legacy_data()
    if args.headless or not sys.stdout.isatty():
        return asyncio.run(_run_headless(args, migrated))
    return _run_tui(args, migrated)


# ---- headless -------------------------------------------------------------

async def _run_headless(args, migrated):
    controller = Controller(device=args.device, reconnect=not args.no_reconnect)

    def emit(line):
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}", flush=True)

    controller.log_listeners.append(emit)
    emit(f"-- MeshHunter headless · config {CONFIG_PATH} · data {DATA_DIR} --")
    if migrated:
        emit(f"-- Migrated legacy data files: {', '.join(migrated)} --")
    if not controller.device:
        emit("-- No device: pass --device /dev/ttyXXX (see --list-devices) --")
        return 2

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, ValueError):
            pass
    controller.start(autoconnect=True)
    await stop.wait()
    emit("-- Shutting down --")
    await controller.shutdown()
    return 0


# ---- TUI ------------------------------------------------------------------

def _run_tui(args, migrated):
    try:
        import curses
    except ImportError:
        print("The TUI needs curses (on Windows: pip install windows-curses). "
              "Or use --headless.", file=sys.stderr)
        return 1
    os.environ.setdefault("ESCDELAY", "25")  # don't make Esc wait a second

    def wrapped(stdscr):
        return asyncio.run(_tui_main(stdscr, args, migrated))

    return curses.wrapper(wrapped)


async def _tui_main(stdscr, args, migrated):
    from .screen import Screen

    controller = Controller(device=args.device, reconnect=not args.no_reconnect)
    screen = Screen(stdscr, controller)
    controller.log_line(f"-- MeshHunter TUI · config {CONFIG_PATH} · data {DATA_DIR} --")
    if migrated:
        controller.log_line(f"-- Migrated legacy data files: {', '.join(migrated)} --")
    controller.start(autoconnect=bool(args.connect or args.device) and bool(controller.device))
    if not controller.device:
        controller.log_line("-- Press d to choose a device --")
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, screen._quit)
    except (NotImplementedError, ValueError):
        pass
    try:
        await screen.run()
    finally:
        await controller.shutdown()
    return 0
