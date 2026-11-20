"""Minimal receive-only SX1262 driver for a radio on a CH341 USB bridge.

Only what the sniffer needs: init, retune to a LoRa profile, continuous
receive, IRQ polling, packet/RSSI readout. Opcodes and register addresses
are from the Semtech SX1261/2 datasheet (rev 2.1); the init sequence and
values mirror what RadioLib does on the XIAO sniffer, so both radios
behave the same.

Pin assignment defaults to the MeshToad's (meshtasticd's
lora-usb-meshtoad-e22.yaml): CS=D0, RXEN=D1, RESET=D2, BUSY=D4, DIO1=D6;
DIO2 switches the PA, DIO3 powers a 1.8 V TCXO.
"""

import math
import time

from .ch341 import Ch341, Ch341Error

# Opcodes
SET_STANDBY = 0x80
SET_RX = 0x82
SET_RF_FREQUENCY = 0x86
SET_PACKET_TYPE = 0x8A
SET_MODULATION_PARAMS = 0x8B
SET_PACKET_PARAMS = 0x8C
SET_BUFFER_BASE = 0x8F
CALIBRATE = 0x89
SET_REGULATOR_MODE = 0x96
SET_DIO3_TCXO = 0x97
CALIBRATE_IMAGE = 0x98
SET_DIO2_RF_SWITCH = 0x9D
SET_DIO_IRQ_PARAMS = 0x08
CLEAR_IRQ = 0x02
GET_IRQ = 0x12
GET_RX_BUFFER_STATUS = 0x13
GET_PACKET_STATUS = 0x14
GET_RSSI_INST = 0x15
GET_STATUS = 0xC0
WRITE_REGISTER = 0x0D
READ_REGISTER = 0x1D
READ_BUFFER = 0x1E

REG_SYNC_WORD = 0x0740
REG_RX_GAIN = 0x08AC
RX_GAIN_BOOSTED = 0x96

# IRQ bits
IRQ_RX_DONE = 0x0002
IRQ_PREAMBLE = 0x0004
IRQ_HEADER_VALID = 0x0010
IRQ_HEADER_ERR = 0x0020
IRQ_CRC_ERR = 0x0040
IRQ_TIMEOUT = 0x0200
IRQ_ALL = 0x03FF

BW_CODES = {7.8: 0x00, 10.4: 0x08, 15.6: 0x01, 20.8: 0x09, 31.25: 0x02,
            41.7: 0x0A, 62.5: 0x03, 125.0: 0x04, 250.0: 0x05, 500.0: 0x06}
TCXO_CODES = {1.6: 0x00, 1.7: 0x01, 1.8: 0x02, 2.2: 0x03, 2.4: 0x04, 2.7: 0x05, 3.0: 0x06, 3.3: 0x07}

MESHTOAD_PINS = {"cs": 0, "rxen": 1, "reset": 2, "busy": 4, "dio1": 6}


class RadioError(Exception):
    pass


def _bw_code(bw):
    for khz, code in BW_CODES.items():
        if abs(khz - bw) < 0.05:
            return code
    raise RadioError(f"unsupported bandwidth {bw} kHz")


def time_on_air_ms(sf, bw, cr, preamble, length, crc=True, explicit=True):
    """LoRa time on air in ms (Semtech AN1200.13; SF7-12, cr = 5..8 for 4/5..4/8)."""
    sym = (1 << sf) / bw
    de = 1 if sym >= 16.0 else 0
    payload_bits = 8 * length - 4 * sf + 28 + (16 if crc else 0) - (0 if explicit else 20)
    n_payload = 8 + max(math.ceil(payload_bits / (4 * (sf - 2 * de))), 0) * cr
    return (preamble + 4.25 + n_payload) * sym


class SX1262:
    def __init__(self, serial=None, pins=None, tcxo_volts=1.8, boosted_rx=True):
        self.pins = dict(MESHTOAD_PINS, **(pins or {}))
        self.tcxo_volts = tcxo_volts
        self.boosted_rx = boosted_rx
        self.bus = Ch341(serial)
        self.serial = self.bus.serial
        self._busy_after = False

    def close(self):
        try:
            self.standby()
            self.bus.set_pin(self.pins["rxen"], 0)
            self.bus.apply_pins()
        except (Ch341Error, RadioError):
            pass
        self.bus.close()

    # ---- low level --------------------------------------------------------

    def _wait_busy(self, timeout=0.2):
        deadline = time.monotonic() + timeout
        busy_mask = 1 << self.pins["busy"]
        while self.bus.read_pins() & busy_mask:
            if time.monotonic() > deadline:
                raise RadioError("SX1262 stuck busy (no radio, or not powered?)")
            time.sleep(0.0005)

    def cmd(self, opcode, *args, read=0, settle_us=60):
        """Send opcode+args, clocking `read` extra bytes back. Returns the
        bytes after the opcode/args."""
        if self._busy_after:
            self._wait_busy()
        data = bytes([opcode, *args]) + bytes(read)
        reply, pins = self.bus.command(self.pins["cs"], data, settle_us)
        self._busy_after = bool(pins & (1 << self.pins["busy"]))
        return reply[1 + len(args):]

    def write_register(self, addr, *values):
        self.cmd(WRITE_REGISTER, addr >> 8, addr & 0xFF, *values)

    def read_register(self, addr, count=1):
        # The NOP status byte after the address is part of the args, so
        # what's left is exactly the register contents.
        return self.cmd(READ_REGISTER, addr >> 8, addr & 0xFF, 0, read=count)

    # ---- setup ------------------------------------------------------------

    def reset(self):
        p = self.pins
        self.bus.set_pin(p["cs"], 1)
        self.bus.set_pin(p["rxen"], 0)
        self.bus.set_pin(3, 0)  # SCK idle low (SPI mode 0)
        self.bus.set_pin(5, 1)  # MOSI
        self.bus.set_pin(p["busy"], 0, output=False)
        self.bus.set_pin(p["reset"], 0)
        self.bus.apply_pins(delay_us=200)
        self.bus.set_pin(p["reset"], 1)
        self.bus.apply_pins()
        time.sleep(0.01)
        self._wait_busy(timeout=0.5)
        self._busy_after = False

    def begin(self):
        """Reset and configure for LoRa receive. Raises RadioError if no
        SX126x answers."""
        self.reset()
        self.standby()
        # Sync word register reads 0x1424 after reset on every SX126x:
        # a cheap "is there really a radio on this bus" check.
        sw = self.read_register(REG_SYNC_WORD, 2)
        if sw != b"\x14\x24":
            raise RadioError(f"no SX126x answering on the CH341 (sync register read {sw.hex()})")
        if self.tcxo_volts:
            delay = 320  # 5 ms in 15.625 us steps, like RadioLib
            self.cmd(SET_DIO3_TCXO, TCXO_CODES[self.tcxo_volts], delay >> 16, (delay >> 8) & 0xFF, delay & 0xFF)
            self.cmd(CALIBRATE, 0x7F, settle_us=200)
            self._wait_busy(timeout=0.1)
            self.standby()
        self.cmd(SET_REGULATOR_MODE, 0x01)  # DC-DC
        self.cmd(SET_PACKET_TYPE, 0x01)  # LoRa
        self.cmd(SET_DIO2_RF_SWITCH, 0x01)
        self.cmd(SET_BUFFER_BASE, 0x00, 0x00)
        # Image calibration once for the whole US 902-928 MHz band, so
        # hopping can retune without recalibrating (same as the XIAO).
        self.cmd(CALIBRATE_IMAGE, 0xE1, 0xE9, settle_us=200)
        self._wait_busy(timeout=0.1)
        irq = IRQ_RX_DONE | IRQ_PREAMBLE | IRQ_HEADER_VALID | IRQ_HEADER_ERR | IRQ_CRC_ERR | IRQ_TIMEOUT
        self.cmd(SET_DIO_IRQ_PARAMS, irq >> 8, irq & 0xFF, irq >> 8, irq & 0xFF, 0, 0, 0, 0)
        # RX path on: the E22's RXEN switch (DIO2 handles TXEN).
        self.bus.set_pin(self.pins["rxen"], 1)
        self.bus.apply_pins()

    def standby(self):
        self.cmd(SET_STANDBY, 0x00)

    # ---- per profile ------------------------------------------------------

    def configure(self, freq_mhz, bw, sf, cr, sync, preamble):
        self.standby()
        frf = round(freq_mhz * 1e6 * (1 << 25) / 32e6)
        self.cmd(SET_RF_FREQUENCY, (frf >> 24) & 0xFF, (frf >> 16) & 0xFF, (frf >> 8) & 0xFF, frf & 0xFF)
        ldro = 1 if (1 << sf) / bw >= 16.0 else 0
        self.cmd(SET_MODULATION_PARAMS, sf, _bw_code(bw), cr - 4, ldro)
        # explicit header, max payload 255, CRC on, standard IQ
        self.cmd(SET_PACKET_PARAMS, preamble >> 8, preamble & 0xFF, 0x00, 0xFF, 0x01, 0x00)
        self.write_register(REG_SYNC_WORD, (sync & 0xF0) | 0x04, ((sync & 0x0F) << 4) | 0x04)

    def start_rx(self):
        self.cmd(CLEAR_IRQ, IRQ_ALL >> 8, IRQ_ALL & 0xFF)
        if self.boosted_rx:
            self.write_register(REG_RX_GAIN, RX_GAIN_BOOSTED)
        self.cmd(SET_RX, 0xFF, 0xFF, 0xFF)  # continuous

    # ---- status -----------------------------------------------------------

    def irq_status(self):
        r = self.cmd(GET_IRQ, read=3)
        return (r[1] << 8) | r[2]

    def rssi_inst(self):
        return -self.cmd(GET_RSSI_INST, read=2)[1] / 2.0

    def read_packet(self):
        """(payload bytes, rssi dBm, snr dB) for the packet just received."""
        st = self.cmd(GET_RX_BUFFER_STATUS, read=3)
        length, start = st[1], st[2]
        ps = self.cmd(GET_PACKET_STATUS, read=4)
        rssi = -ps[1] / 2.0
        snr = (ps[2] - 256 if ps[2] > 127 else ps[2]) / 4.0
        data = self.cmd(READ_BUFFER, start, read=length + 1)[1:] if length else b""
        return bytes(data), rssi, snr
