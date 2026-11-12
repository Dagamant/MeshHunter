"""Minimal receive-only Semtech SX1262 driver over Linux spidev + libgpiod.

Just enough of the SX126x command set to put the radio into continuous
LoRa receive with a given frequency/SF/BW/CR/sync word and pull packets
out as they arrive -- no transmit path at all, so MeshHunter can never key
up the radio. Defaults match the HackerGadgets RTL-SDR/LoRa/GPS/RTC HAT
(spidev1.0, NSS on GPIO18 via the spi1-1cs overlay, BUSY 24, RESET 25,
DIO1 26, DIO2 drives the RF switch, DIO3 powers the TCXO).

Linux-only: spidev and gpiod are imported lazily by open() so the rest of
the app still imports on Windows/macOS.
"""

import glob
import time
from datetime import timedelta

# SX126x opcodes (datasheet section 13)
_SET_STANDBY = 0x80
_SET_RX = 0x82
_SET_RF_FREQUENCY = 0x86
_CALIBRATE = 0x89
_SET_PACKET_TYPE = 0x8A
_SET_MODULATION_PARAMS = 0x8B
_SET_PACKET_PARAMS = 0x8C
_SET_BUFFER_BASE_ADDRESS = 0x8F
_SET_REGULATOR_MODE = 0x96
_SET_DIO3_AS_TCXO_CTRL = 0x97
_CALIBRATE_IMAGE = 0x98
_SET_DIO2_AS_RF_SWITCH_CTRL = 0x9D
_SET_DIO_IRQ_PARAMS = 0x08
_CLEAR_IRQ_STATUS = 0x02
_GET_IRQ_STATUS = 0x12
_GET_RX_BUFFER_STATUS = 0x13
_GET_PACKET_STATUS = 0x14
_GET_STATUS = 0xC0
_WRITE_REGISTER = 0x0D
_READ_REGISTER = 0x1D
_READ_BUFFER = 0x1E

_REG_LORA_SYNC_WORD = 0x0740
_REG_IQ_POLARITY = 0x0736
_REG_RX_GAIN = 0x08AC

_IRQ_RX_DONE = 0x0002
_IRQ_HEADER_ERR = 0x0020
_IRQ_CRC_ERR = 0x0040
_IRQ_ALL = 0x03FF

# kHz -> SX126x LoRa bandwidth code
_BW_CODES = {
    7.8: 0x00, 10.4: 0x08, 15.6: 0x01, 20.8: 0x09, 31.25: 0x02,
    41.7: 0x0A, 62.5: 0x03, 125.0: 0x04, 250.0: 0x05, 500.0: 0x06,
}

_TCXO_1V8 = 0x02
_XTAL_HZ = 32_000_000


class SX1262Error(RuntimeError):
    pass


class RxPacket:
    __slots__ = ("payload", "rssi", "snr", "crc_error")

    def __init__(self, payload, rssi, snr, crc_error=False):
        self.payload = payload
        self.rssi = rssi
        self.snr = snr
        self.crc_error = crc_error


def _find_gpio_chip(line_name):
    """Path of the gpiochip that exposes the 40-pin header's `line_name`
    (e.g. "GPIO26") -- gpiochip0 on a Pi 5, but numbering has moved
    between kernels/models, so look it up by name rather than hardcode."""
    import gpiod

    for path in sorted(glob.glob("/dev/gpiochip*")):
        try:
            with gpiod.Chip(path) as chip:
                chip.line_offset_from_id(line_name)
                return path
        except (OSError, ValueError):
            continue
    raise SX1262Error(f"No gpiochip exposes {line_name}")


class SX1262:
    def __init__(self, spidev="/dev/spidev1.0", busy=24, reset=25, dio1=26, tcxo_voltage=_TCXO_1V8):
        self.spidev_path = spidev
        self.busy_pin = busy
        self.reset_pin = reset
        self.dio1_pin = dio1
        self.tcxo_voltage = tcxo_voltage
        self._spi = None
        self._lines = None

    # ---- lifecycle --------------------------------------------------------

    def open(self):
        import gpiod
        import spidev
        from gpiod.line import Direction, Edge, Value

        bus, dev = (int(x) for x in self.spidev_path.rsplit("spidev", 1)[1].split("."))
        self._spi = spidev.SpiDev()
        self._spi.open(bus, dev)
        self._spi.max_speed_hz = 8_000_000
        self._spi.mode = 0

        chip_path = _find_gpio_chip(f"GPIO{self.dio1_pin}")
        self._lines = gpiod.request_lines(
            chip_path,
            consumer="meshhunter-sx1262",
            config={
                self.busy_pin: gpiod.LineSettings(direction=Direction.INPUT),
                self.dio1_pin: gpiod.LineSettings(direction=Direction.INPUT, edge_detection=Edge.RISING),
                self.reset_pin: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE),
            },
        )
        self._value = Value
        self.reset()

    def close(self):
        if self._spi is not None:
            try:
                self._cmd(_SET_STANDBY, [0x00])
            except Exception:
                pass
            self._spi.close()
            self._spi = None
        if self._lines is not None:
            self._lines.release()
            self._lines = None

    def reset(self):
        self._lines.set_value(self.reset_pin, self._value.INACTIVE)
        time.sleep(0.002)
        self._lines.set_value(self.reset_pin, self._value.ACTIVE)
        time.sleep(0.010)
        self._wait_busy(timeout=1.0)

    # ---- low-level --------------------------------------------------------

    def _wait_busy(self, timeout=0.5):
        deadline = time.monotonic() + timeout
        while self._lines.get_value(self.busy_pin) == self._value.ACTIVE:
            if time.monotonic() > deadline:
                raise SX1262Error("SX1262 BUSY stuck high -- check spidev/pins and that nothing else owns the radio")
            time.sleep(0.0002)

    def _xfer(self, data):
        self._wait_busy()
        return self._spi.xfer2(list(data))

    def _cmd(self, opcode, args=()):
        return self._xfer([opcode, *args])

    def _read_cmd(self, opcode, n):
        # Response: opcode byte clocks out garbage, then a status byte, then n bytes.
        return self._xfer([opcode, 0x00] + [0x00] * n)[2:]

    def _write_register(self, addr, data):
        self._cmd(_WRITE_REGISTER, [(addr >> 8) & 0xFF, addr & 0xFF, *data])

    def _read_register(self, addr, n=1):
        return self._xfer([_READ_REGISTER, (addr >> 8) & 0xFF, addr & 0xFF, 0x00] + [0x00] * n)[4:]

    def status(self):
        return self._xfer([_GET_STATUS, 0x00])[1]

    # ---- configuration ----------------------------------------------------

    def configure_lora_rx(self, freq_hz, sf, bw_khz, cr, sync_word, preamble=16):
        """Put the radio into continuous LoRa RX with these parameters.
        cr is the 4/x denominator (5..8). sync_word is the one-byte
        "public" LoRa sync word form (0x12 MeshCore, 0x2B Meshtastic)."""
        if bw_khz not in _BW_CODES:
            raise SX1262Error(f"Unsupported bandwidth {bw_khz} kHz")
        if not 5 <= cr <= 8:
            raise SX1262Error(f"Coding rate must be 4/5..4/8, got 4/{cr}")

        self._cmd(_SET_STANDBY, [0x00])  # STDBY_RC
        # TCXO first: with no clock the calibration below can't complete.
        # 5 ms startup delay, in 15.625 us steps.
        delay = 320
        self._cmd(_SET_DIO3_AS_TCXO_CTRL, [self.tcxo_voltage, (delay >> 16) & 0xFF, (delay >> 8) & 0xFF, delay & 0xFF])
        self._cmd(_CALIBRATE, [0x7F])
        time.sleep(0.005)
        self._wait_busy()
        self._cmd(_SET_REGULATOR_MODE, [0x01])  # DC-DC
        self._cmd(_SET_DIO2_AS_RF_SWITCH_CTRL, [0x01])
        self._cmd(_SET_PACKET_TYPE, [0x01])  # LoRa

        self._cmd(_CALIBRATE_IMAGE, list(_image_cal_band(freq_hz)))
        freq_reg = int(freq_hz * (1 << 25) / _XTAL_HZ)
        self._cmd(_SET_RF_FREQUENCY, list(freq_reg.to_bytes(4, "big")))
        self._cmd(_SET_BUFFER_BASE_ADDRESS, [0x00, 0x00])

        symbol_ms = (1 << sf) / bw_khz
        ldro = 0x01 if symbol_ms >= 16.38 else 0x00
        self._cmd(_SET_MODULATION_PARAMS, [sf, _BW_CODES[bw_khz], cr - 4, ldro])
        # Explicit header, max payload, CRC on, standard IQ.
        self._cmd(_SET_PACKET_PARAMS, [(preamble >> 8) & 0xFF, preamble & 0xFF, 0x00, 0xFF, 0x01, 0x00])
        # Datasheet 15.4 errata: standard IQ needs bit 2 of 0x0736 set.
        iq = self._read_register(_REG_IQ_POLARITY)[0]
        self._write_register(_REG_IQ_POLARITY, [iq | 0x04])

        # Same nibble expansion RadioLib uses (control bits 0x44).
        self._write_register(_REG_LORA_SYNC_WORD, [(sync_word & 0xF0) | 0x04, ((sync_word & 0x0F) << 4) | 0x04])
        self._write_register(_REG_RX_GAIN, [0x96])  # boosted RX gain

        irq = _IRQ_RX_DONE | _IRQ_CRC_ERR | _IRQ_HEADER_ERR
        self._cmd(_SET_DIO_IRQ_PARAMS, [irq >> 8, irq & 0xFF, irq >> 8, irq & 0xFF, 0, 0, 0, 0])
        self._cmd(_CLEAR_IRQ_STATUS, [_IRQ_ALL >> 8, _IRQ_ALL & 0xFF])
        self._cmd(_SET_RX, [0xFF, 0xFF, 0xFF])  # continuous

        readback = self._read_register(_REG_LORA_SYNC_WORD, 2)
        expected = [(sync_word & 0xF0) | 0x04, ((sync_word & 0x0F) << 4) | 0x04]
        if readback != expected:
            raise SX1262Error(f"SX1262 not responding (sync word readback {readback}) -- is /dev/spidev1.0 the radio?")

    # ---- receive ------------------------------------------------------------

    def receive(self, timeout=0.5):
        """Wait up to `timeout` seconds for a packet. Returns an RxPacket,
        or None if nothing arrived (or only a header error)."""
        if self._lines.get_value(self.dio1_pin) != self._value.ACTIVE:
            if self._lines.wait_edge_events(timedelta(seconds=timeout)):
                self._lines.read_edge_events()
        irq_bytes = self._read_cmd(_GET_IRQ_STATUS, 2)
        irq = (irq_bytes[0] << 8) | irq_bytes[1]
        if not irq:
            return None
        self._cmd(_CLEAR_IRQ_STATUS, [_IRQ_ALL >> 8, _IRQ_ALL & 0xFF])
        if not irq & _IRQ_RX_DONE:
            return None

        length, start = self._read_cmd(_GET_RX_BUFFER_STATUS, 2)
        payload = bytes(self._xfer([_READ_BUFFER, start, 0x00] + [0x00] * length)[3:]) if length else b""
        rssi_raw, snr_raw, _ = self._read_cmd(_GET_PACKET_STATUS, 3)
        snr = (snr_raw - 256 if snr_raw > 127 else snr_raw) / 4.0
        return RxPacket(payload, -rssi_raw / 2.0, snr, crc_error=bool(irq & _IRQ_CRC_ERR))


def _image_cal_band(freq_hz):
    mhz = freq_hz / 1e6
    if mhz > 900:
        return 0xE1, 0xE9
    if mhz > 850:
        return 0xD7, 0xDB
    if mhz > 770:
        return 0xC1, 0xC5
    if mhz > 460:
        return 0x75, 0x81
    return 0x6B, 0x6F
