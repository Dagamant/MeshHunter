"""The sniffer firmware, run on the host for USB LoRa sticks.

A stick like the MeshToad is just an SX1262 behind a CH341 USB bridge,
with no microcontroller of its own, so the logic the XIAO runs in
firmware/sniffer/src/main.cpp (listen / lock onto a preamble / linger /
hop) runs here instead, on its own thread, driving the radio over USB.

UsbRadioPort looks like the pyserial port SnifferWorker normally opens:
the same text commands go in through write(), and the same JSON event
lines come out of readline(). So everything downstream (configuration,
decoding, the waterfall, logging) is shared with the XIAO unchanged.
See the firmware's header comment for the protocol and hop behaviour.

Differences from the XIAO: every radio operation is a ~1 ms USB round
trip instead of microseconds, so retuning costs ~10-15 ms and the IRQ
poll runs every few ms. Fixed mode is unaffected; hop mode gets a dwell
floor (MIN_DWELL_MS) so a hop isn't mostly retune time.
"""

import json
import math
import queue
import threading
import time

from .ch341 import Ch341Error
from .sx1262 import (
    IRQ_CRC_ERR,
    IRQ_HEADER_ERR,
    IRQ_HEADER_VALID,
    IRQ_PREAMBLE,
    IRQ_RX_DONE,
    SX1262,
    RadioError,
    time_on_air_ms,
)

FW_NAME = "meshhunter-usb-radio"
FW_VERSION = 1
MAX_PROFILES = 16
STAT_INTERVAL_MS = 5000
RSSI_REPORT_MS = 200
RSSI_SAMPLE_MS = 20
RSSI_SETTLE_MS = 3
MIN_DWELL_MS = 30


def _now_ms():
    return int(time.monotonic() * 1000)


class _Profile:
    def __init__(self, pid, freq, bw, sf, cr, sync, preamble):
        self.id, self.freq, self.bw, self.sf, self.cr = pid, freq, bw, sf, cr
        self.sync, self.preamble = sync, preamble
        self.reset_counters()

    def reset_counters(self):
        self.rx_ok = self.rx_crc = self.hdr_err = self.timeouts = self.pre_only = 0
        self.listen_ms = 0

    def symbol_ms(self):
        return (1 << self.sf) / self.bw

    def fields(self):
        return {"p": self.id, "f": round(self.freq, 4), "bw": self.bw, "sf": self.sf, "cr": self.cr, "sw": self.sync}


class UsbRadioPort:
    """pyserial-shaped wrapper around the host-side sniffer loop."""

    def __init__(self, serial=None):
        try:
            self.radio = SX1262(serial)
        except Ch341Error as exc:
            raise OSError(str(exc)) from exc
        try:
            self.radio.begin()
        except (Ch341Error, RadioError) as exc:
            self.radio.close()
            raise OSError(f"USB LoRa radio didn't start: {exc}") from exc
        self.serial = self.radio.serial
        self._out = queue.Queue()
        self._cmds = queue.Queue()
        self._buf = ""
        self._stop = threading.Event()
        self.profiles = []
        self.dwell_cfg, self.linger, self.partial_linger, self.linger_max = 0, 3000, 2000, 12000
        self.state = "idle"
        self.cur = 0
        self.hops = 0
        self.entered_at = _now_ms()
        self.rs_max = {}
        self._thread = threading.Thread(target=self._loop, name="usb-radio", daemon=True)
        self._thread.start()

    # ---- pyserial-ish interface --------------------------------------------

    def write(self, data):
        self._buf += data.decode(errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._cmds.put(line.strip())

    def flush(self):
        pass

    def readline(self, timeout=0.2):
        try:
            line = self._out.get(timeout=timeout)
        except queue.Empty:
            return b""
        if line is None:
            self._out.put(None)  # stay failed for any later reads
            raise OSError("USB radio stopped responding (unplugged?)")
        return line

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self.radio.close()

    # ---- output ---------------------------------------------------------------

    def _emit(self, **obj):
        self._out.put((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def _err(self, msg, code=0):
        self._emit(ev="err", msg=msg, code=code)

    # ---- main loop ------------------------------------------------------------

    def _loop(self):
        last_stat = last_rs = last_sample = _now_ms()
        try:
            while not self._stop.is_set():
                while True:
                    try:
                        self._command(self._cmds.get_nowait())
                    except queue.Empty:
                        break
                if self.state == "idle":
                    time.sleep(0.005)
                    continue
                now = _now_ms()
                self._poll_radio(now)
                if self.state == "idle":
                    continue
                if now - last_sample >= RSSI_SAMPLE_MS:
                    last_sample = now
                    self._sample_rssi(now)
                if now - last_rs >= RSSI_REPORT_MS:
                    last_rs = now
                    self._send_rssi()
                if now - last_stat >= STAT_INTERVAL_MS:
                    last_stat = now
                    self._send_stat(now)
        except (Ch341Error, RadioError) as exc:
            # Unplugged or wedged: report it the way a serial port would fail.
            self._emit(ev="err", msg=f"USB radio error: {exc}", code=-1)
            self._out.put(None)

    # ---- commands -------------------------------------------------------------

    def _command(self, line):
        argv = line.split()
        if not argv:
            return
        cmd = argv[0]
        if cmd == "HELLO":
            self._emit(ev="hello", fw=FW_NAME, ver=FW_VERSION, radio=0,
                       running=0 if self.state == "idle" else 1, max_profiles=MAX_PROFILES)
        elif cmd == "STOP":
            self._stop_radio()
            self._emit(ev="ok", cmd="STOP")
        elif cmd == "CLR":
            self._stop_radio()
            self.profiles = []
            self._emit(ev="ok", cmd="CLR")
        elif cmd == "P":
            if len(argv) != 8:
                return self._err("P needs 7 args")
            if len(self.profiles) >= MAX_PROFILES:
                return self._err("too many profiles")
            try:
                p = _Profile(int(argv[1]), float(argv[2]), float(argv[3]), int(argv[4]),
                             int(argv[5]), int(argv[6], 16), int(argv[7]))
            except ValueError:
                return self._err("bad profile")
            if not (150 <= p.freq <= 960 and 5 <= p.sf <= 12 and 5 <= p.cr <= 8 and p.bw >= 7):
                return self._err("bad profile")
            self.profiles.append(p)
            self._emit(ev="ok", cmd="P")
        elif cmd == "T":
            if len(argv) != 5:
                return self._err("T needs 4 args")
            try:
                self.dwell_cfg, self.linger, self.partial_linger, self.linger_max = (int(a) for a in argv[1:])
            except ValueError:
                return self._err("bad timing")
            self._emit(ev="ok", cmd="T")
        elif cmd == "GO":
            if not self.profiles:
                return self._err("no profiles")
            for p in self.profiles:
                p.reset_counters()
            self.hops = 0
            self.state = "listen"
            self.entered_at = _now_ms()
            self._enter(0)
            self._emit(ev="ok", cmd="GO")
        else:
            self._err("unknown command")

    # ---- radio state machine (mirrors firmware/sniffer/src/main.cpp) -----------

    def _dwell(self, p):
        if self.dwell_cfg:
            return max(self.dwell_cfg, MIN_DWELL_MS)
        return max(MIN_DWELL_MS, math.ceil(p.symbol_ms() * 10))

    def _enter(self, idx):
        now = _now_ms()
        if self.state != "idle" and self.profiles:
            self.profiles[self.cur].listen_ms += now - self.entered_at
        self.cur = idx
        p = self.profiles[idx]
        self.lingering = False
        self.linger_until = 0
        try:
            self.radio.configure(p.freq, p.bw, p.sf, p.cr, p.sync, p.preamble)
        except RadioError as exc:
            self._err(f"profile apply failed: {exc}")
            return
        self.entered_at = _now_ms()
        self.dwell_end = self.entered_at + self._dwell(p)
        self._start_rx()

    def _start_rx(self):
        self.radio.start_rx()
        self.state = "listen"
        self.lock_header = False

    def _stop_radio(self):
        if self.state != "idle" and self.profiles:
            self.profiles[self.cur].listen_ms += _now_ms() - self.entered_at
        if self.state != "idle":
            self.radio.standby()
        self.state = "idle"

    def _extend_linger(self, ms):
        if len(self.profiles) < 2:
            return
        now = _now_ms()
        if not self.lingering:
            self.lingering = True
            self.linger_start = now
        until = min(now + ms, self.linger_start + self.linger_max)
        self.linger_until = max(self.linger_until, until)

    def _poll_radio(self, now):
        p = self.profiles[self.cur]
        irq = self.radio.irq_status()
        if irq & IRQ_RX_DONE:
            data, rssi, snr = self.radio.read_packet()
            crc_ok = not (irq & IRQ_CRC_ERR)
            self._emit(ev="pkt", **p.fields(), rssi=rssi, snr=snr, crc=1 if crc_ok else 0,
                       ms=now, hex=data.hex())
            if crc_ok:
                p.rx_ok += 1
                self._extend_linger(self.linger)
            else:
                p.rx_crc += 1
                self._extend_linger(self.partial_linger)
            self._start_rx()
            return
        if irq & IRQ_HEADER_ERR:
            p.hdr_err += 1
            self._emit(ev="partial", kind="header", **p.fields(), rssi=self.radio.rssi_inst(), ms=now)
            self._extend_linger(self.partial_linger)
            self._start_rx()
            return
        if self.state == "listen" and irq & (IRQ_PREAMBLE | IRQ_HEADER_VALID):
            self.state = "locked"
            self.lock_header = False
            # Rest of the preamble + sync + header. The detector fires early in
            # the preamble and senders' preambles vary (MeshCore's measured
            # ~32 symbols: header came 42 symbols after detection), so allow
            # 64 symbols or preamble+32, whichever is longer.
            self.lock_deadline = now + math.ceil(p.symbol_ms() * max(64, p.preamble + 32)) + 15
        if self.state == "locked":
            if not self.lock_header and irq & IRQ_HEADER_VALID:
                self.lock_header = True
                self.lock_deadline = now + math.ceil(time_on_air_ms(p.sf, p.bw, p.cr, p.preamble, 255)) + 50
            if now > self.lock_deadline:
                if self.lock_header:
                    p.timeouts += 1
                    self._emit(ev="partial", kind="timeout", **p.fields(), rssi=self.radio.rssi_inst(), ms=now)
                    self._extend_linger(self.partial_linger)
                else:
                    p.pre_only += 1
                self._start_rx()
            return
        if len(self.profiles) > 1:
            dwell_done = now >= self.dwell_end
            linger_done = not self.lingering or now >= self.linger_until
            if dwell_done and linger_done:
                self.hops += 1
                self._enter((self.cur + 1) % len(self.profiles))

    # ---- telemetry ------------------------------------------------------------

    def _sample_rssi(self, now):
        if now - self.entered_at < RSSI_SETTLE_MS or self.state == "idle":
            return
        r = self.radio.rssi_inst()
        best = self.rs_max.get(self.cur)
        if best is None or r > best:
            self.rs_max[self.cur] = r

    def _send_rssi(self):
        values = [[self.profiles[i].id, v] for i, v in sorted(self.rs_max.items()) if i < len(self.profiles)]
        self.rs_max = {}
        self._emit(ev="rs", cur=self.profiles[self.cur].id, v=values)

    def _send_stat(self, now):
        rows = []
        for i, p in enumerate(self.profiles):
            live = now - self.entered_at if (self.state != "idle" and i == self.cur) else 0
            rows.append([p.id, p.rx_ok, p.rx_crc, p.hdr_err, p.timeouts, p.pre_only, p.listen_ms + live])
        self._emit(ev="stat", ms=now, cur=self.profiles[self.cur].id, hops=self.hops,
                   nf=self.radio.rssi_inst(), profiles=rows)
