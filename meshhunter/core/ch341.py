"""CH341 USB-to-SPI/GPIO bridge, as used by USB LoRa sticks such as the
Nullhop MeshToad and MeshStick (USB 1a86:5512, the CH341 in its
"EPP/MEM/I2C" mode, with no kernel driver bound).

Protocol (from the CH341 datasheet, flashrom's ch341a_spi and Pine64's
libpinedio-usb, which meshtasticd uses for these sticks):

- Bulk OUT 0x02 / bulk IN 0x82, 32-byte USB packets. Each OUT packet is
  one command byte plus up to 31 bytes of data.
- 0xA8 SPI stream: clocks the packet's data out (full duplex); the stick
  answers each packet with as many bytes as it clocked, in its own short
  IN packet. The CH341 shifts LSB first, so every byte is bit-reversed
  both ways to talk to an MSB-first device like the SX1262.
- 0xAB UIO stream: 0x80|bits sets D0-D5, 0x40|bits sets their direction
  (1 = output), 0xC0|n waits n microseconds (n <= 63), 0x20 ends.
- 0xA0: read the input pins; 6 bytes back, byte 0 = D0-D7.

SPI uses D3 (SCK), D5 (MOSI) and D7 (MISO); the other D pins are GPIO.

Every USB round trip costs about a millisecond (full-speed USB), so
`command()` packs a whole SX1262 transaction into two transfers: [CS low
+ SPI], then [CS high (which is what makes the SX1262 execute it) + a
short wait + read pins], which also returns BUSY for free.
"""

import os

import usb.core
import usb.util

VID = 0x1A86
PID = 0x5512

EP_OUT = 0x02
EP_IN = 0x82
PACKET = 32
TIMEOUT_MS = 1000
_SYSFS_USB = "/sys/bus/usb/devices"

CMD_GET_INPUT = 0xA0
CMD_SPI_STREAM = 0xA8
CMD_UIO_STREAM = 0xAB
UIO_OUT = 0x80
UIO_DIR = 0x40
UIO_END = 0x20
UIO_US = 0xC0

_REVERSE = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


class Ch341Error(Exception):
    pass


def find_devices():
    """[(serial, usb.core.Device)] for every CH341 in SPI/I2C mode. The
    serial is read without claiming the device, so this is safe to call
    while another process is using one -- but it needs read access to the
    USB device node, which is where a missing udev rule shows up."""
    out = []
    for dev in usb.core.find(find_all=True, idVendor=VID, idProduct=PID) or []:
        serial = _sysfs_serial(dev)
        if serial is None:
            try:
                serial = usb.util.get_string(dev, dev.iSerialNumber) or ""
            except (usb.core.USBError, ValueError, NotImplementedError):
                serial = ""  # no permission (udev rule missing) or busy
        out.append((serial, dev))
    return out


def _sysfs_serial(dev):
    """The serial the Linux kernel cached at enumeration, or None elsewhere.
    Reading it there costs no USB traffic: a CH341 that's busy streaming
    for another program (or another thread) can drop its bulk replies if a
    string-descriptor request lands in the middle, which knocked running
    sticks offline whenever a second MeshHunter listed devices."""
    try:
        for name in os.listdir(_SYSFS_USB):
            base = os.path.join(_SYSFS_USB, name)
            try:
                with open(os.path.join(base, "busnum")) as f:
                    bus = int(f.read())
                with open(os.path.join(base, "devnum")) as f:
                    addr = int(f.read())
            except (OSError, ValueError):
                continue
            if bus == dev.bus and addr == dev.address:
                try:
                    with open(os.path.join(base, "serial")) as f:
                        return f.read().strip()
                except OSError:
                    return ""
    except OSError:
        pass
    return None


class Ch341:
    def __init__(self, serial=None):
        matches = find_devices()
        if serial:
            matches = [(s, d) for s, d in matches if s == serial]
        if not matches:
            raise Ch341Error(f"No CH341 USB radio found{f' with serial {serial}' if serial else ''}")
        self.serial, self.dev = matches[0]
        try:
            try:
                if self.dev.is_kernel_driver_active(0):
                    self.dev.detach_kernel_driver(0)
            except (NotImplementedError, usb.core.USBError):
                pass
            # Only configure an unconfigured device: SET_CONFIGURATION resets
            # it, which would knock out another program already using it.
            try:
                self.dev.get_active_configuration()
            except usb.core.USBError:
                self.dev.set_configuration()
            usb.util.claim_interface(self.dev, 0)
        except usb.core.USBError as exc:
            if exc.errno == 13:
                raise Ch341Error(
                    "No permission to open the CH341 USB radio. Install the udev rule "
                    "(see firmware/sniffer/README.md) and replug it."
                ) from exc
            if exc.errno == 16:
                raise Ch341Error("The CH341 USB radio is in use by another program (another MeshHunter, or meshtasticd?)") from exc
            raise Ch341Error(f"Could not open the CH341 USB radio: {exc}") from exc
        if not self.serial:
            try:
                self.serial = usb.util.get_string(self.dev, self.dev.iSerialNumber) or ""
            except (usb.core.USBError, ValueError):
                pass
        self.outputs = 0  # D0-D5 output levels
        self.dirs = 0  # D0-D5 directions (1 = output)

    def close(self):
        try:
            usb.util.release_interface(self.dev, 0)
        except usb.core.USBError:
            pass
        usb.util.dispose_resources(self.dev)

    # ---- raw transfers ----------------------------------------------------

    def _write(self, data):
        try:
            self.dev.write(EP_OUT, bytes(data), TIMEOUT_MS)
        except usb.core.USBError as exc:
            raise Ch341Error(f"USB write failed: {exc}") from exc

    def _read(self, count):
        """Read `count` bytes, which the stick sends as one short IN packet
        per command packet (<= 31 bytes each)."""
        buf = bytearray()
        try:
            while len(buf) < count:
                chunk = self.dev.read(EP_IN, PACKET, TIMEOUT_MS)
                if not chunk:
                    break
                buf += bytes(chunk)
        except usb.core.USBError as exc:
            raise Ch341Error(f"USB read failed: {exc}") from exc
        if len(buf) < count:
            raise Ch341Error(f"short USB read ({len(buf)}/{count})")
        return bytes(buf[:count])

    def _uio_packet(self, extra=()):
        """A full 32-byte UIO-stream packet applying the current pin state
        (plus any extra UIO steps, e.g. delays), zero padded so whatever
        follows it in the same transfer starts on a packet boundary."""
        body = [CMD_UIO_STREAM, UIO_OUT | (self.outputs & 0x3F), UIO_DIR | (self.dirs & 0x3F), *extra, UIO_END]
        return bytes(body) + bytes(PACKET - len(body))

    @staticmethod
    def _spi_packets(data):
        out = bytearray()
        for i in range(0, len(data), PACKET - 1):
            chunk = data[i:i + PACKET - 1]
            out.append(CMD_SPI_STREAM)
            out += bytes(_REVERSE[b] for b in chunk)
        return bytes(out)

    # ---- pins ---------------------------------------------------------------

    def set_pin(self, pin, level, output=True):
        """Stage a D0-D5 pin's level/direction (sent with the next transfer)."""
        mask = 1 << pin
        self.dirs = self.dirs | mask if output else self.dirs & ~mask
        self.outputs = self.outputs | mask if level else self.outputs & ~mask

    def apply_pins(self, delay_us=0):
        steps = []
        while delay_us > 0:
            steps.append(UIO_US | min(63, delay_us))
            delay_us -= 63
        self._write(self._uio_packet(steps))

    def read_pins(self):
        """D0-D7 input levels as one byte."""
        self._write(bytes([CMD_GET_INPUT]))
        return self._read(6)[0]

    # ---- SPI ----------------------------------------------------------------

    def command(self, cs_pin, data, settle_us=60):
        """One chip-selected SPI transaction: CS low, clock `data` out (and
        the same number of bytes in), CS high. Returns (reply, pins) where
        pins is D0-D7 read `settle_us` after CS went high."""
        self.set_pin(cs_pin, 0)
        self._write(self._uio_packet() + self._spi_packets(data))
        reply = bytes(_REVERSE[b] for b in self._read(len(data)))
        self.set_pin(cs_pin, 1)
        delays = []
        remaining = settle_us
        while remaining > 0:
            delays.append(UIO_US | min(63, remaining))
            remaining -= 63
        self._write(self._uio_packet(delays) + bytes([CMD_GET_INPUT]))
        pins = self._read(6)[0]
        return reply, pins
