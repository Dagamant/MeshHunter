"""Optional GPS receiver integration: independent hardware/lifecycle from
the MeshCore connection itself."""

import threading

import pynmea2
import serial

from .constants import GPS_BAUD


class GPSState:
    """Latest GPS fix, shared between GPSWorker (one writer) and
    MeshCoreWorker (reader, on a different thread). A plain attribute
    holding a (lat, lon) tuple is replaced wholesale on each update, which
    is atomic under the GIL -- no lock needed, since the only thing that
    matters is never handing out a torn read (lat from one fix, lon from
    another), and there's no requirement to see every intermediate fix.
    """

    def __init__(self):
        self.fix = None

    def update(self, lat, lon):
        self.fix = (lat, lon)


class GPSWorker(threading.Thread):
    """Reads NMEA sentences from a GPS receiver's serial port and keeps a
    GPSState updated with the latest fix. Independent of the MeshCore
    device/connection -- separate hardware, its own lifecycle, running for
    as long as the app is open (or until stop() is called)."""

    RETRY_DELAY = 5.0

    def __init__(self, port, gps_state, ui_queue, baud=GPS_BAUD):
        super().__init__(daemon=True)
        self.port = port
        self.gps_state = gps_state
        self.ui_queue = ui_queue
        self.baud = baud
        self._stop_requested = threading.Event()

    def stop(self):
        self._stop_requested.set()

    def run(self):
        while not self._stop_requested.is_set():
            try:
                self._read_loop()
            except Exception as exc:
                self.ui_queue.put(("log", f"-- GPS error on {self.port}: {exc} --"))
            if self._stop_requested.is_set():
                return
            self._stop_requested.wait(self.RETRY_DELAY)

    def _read_loop(self):
        # Manual buffered read (ser.read(in_waiting) + split on newlines)
        # rather than pyserial's readline(): tested against real hardware
        # (a u-blox 7 USB GPS receiver) where readline() raised
        # SerialException on ~90% of calls at every baud tried, while this
        # approach read cleanly. Likely a readline()-vs-this-driver quirk
        # more than anything baud-rate related, since the failure rate was
        # the same across bauds.
        with serial.Serial(self.port, self.baud, timeout=0.5) as ser:
            self.ui_queue.put(("log", f"-- GPS connected on {self.port} --"))
            had_fix = False
            buf = b""
            while not self._stop_requested.is_set():
                try:
                    chunk = ser.read(ser.in_waiting or 1)
                except serial.SerialException:
                    # Occasional "readiness to read but no data" hiccup
                    # that isn't a real disconnect -- retry rather than
                    # tearing down and reconnecting over a blip.
                    continue
                if not chunk:
                    continue
                buf += chunk
                if len(buf) > 4096:
                    # No newline in 4KB: not NMEA, or badly out of sync.
                    # Drop it rather than let the buffer grow unbounded.
                    buf = buf[-256:]
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    line = raw_line.decode("ascii", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        msg = pynmea2.parse(line)
                    except pynmea2.ParseError:
                        continue

                    lat = getattr(msg, "latitude", None)
                    lon = getattr(msg, "longitude", None)
                    if not lat or not lon:
                        continue
                    # RMC: status 'V' = void/no fix. GGA: gps_qual 0 = no
                    # fix. Sentence types without these fields default to
                    # "accept".
                    if getattr(msg, "status", "A") == "V":
                        continue
                    if getattr(msg, "gps_qual", 1) == 0:
                        continue

                    self.gps_state.update(lat, lon)
                    if not had_fix:
                        self.ui_queue.put(("log", "-- GPS fix acquired --"))
                        had_fix = True
