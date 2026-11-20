"""Thin ctypes wrapper around the system librtlsdr, plus the waterfall
worker thread that turns its IQ stream into FFT rows.

Hand-rolled rather than pyrtlsdr: current pyrtlsdr releases bind symbols
(rtlsdr_set_dithering) that only exist in forked librtlsdr builds, so they
fail to import against Debian/Raspberry Pi OS's stock librtlsdr0. The
handful of calls a waterfall needs are stable across every librtlsdr.
"""

import ctypes
import ctypes.util
import math
import threading

import numpy as np


class RtlSdrError(RuntimeError):
    pass


_lib_cache = None


def rtlsdr_device_count():
    """How many RTL-SDR dongles are plugged in, without opening any (so it
    also counts one that's busy). 0 if librtlsdr isn't installed."""
    global _lib_cache
    try:
        if _lib_cache is None:
            _lib_cache = _load_lib()
        return int(_lib_cache.rtlsdr_get_device_count())
    except (RtlSdrError, OSError):
        return 0


def _load_lib():
    name = ctypes.util.find_library("rtlsdr")
    if not name:
        raise RtlSdrError("librtlsdr not found (Debian/Raspberry Pi OS: sudo apt install rtl-sdr)")
    lib = ctypes.CDLL(name)
    p = ctypes.c_void_p
    lib.rtlsdr_get_device_count.restype = ctypes.c_uint32
    lib.rtlsdr_open.argtypes = [ctypes.POINTER(p), ctypes.c_uint32]
    lib.rtlsdr_close.argtypes = [p]
    lib.rtlsdr_set_sample_rate.argtypes = [p, ctypes.c_uint32]
    lib.rtlsdr_set_center_freq.argtypes = [p, ctypes.c_uint32]
    lib.rtlsdr_set_tuner_gain_mode.argtypes = [p, ctypes.c_int]
    lib.rtlsdr_set_tuner_gain.argtypes = [p, ctypes.c_int]
    lib.rtlsdr_get_tuner_gains.argtypes = [p, ctypes.POINTER(ctypes.c_int)]
    lib.rtlsdr_set_agc_mode.argtypes = [p, ctypes.c_int]
    lib.rtlsdr_reset_buffer.argtypes = [p]
    lib.rtlsdr_read_sync.argtypes = [p, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    return lib


class RtlSdr:
    def __init__(self, index=0):
        self._lib = _load_lib()
        if self._lib.rtlsdr_get_device_count() <= index:
            raise RtlSdrError("No RTL-SDR found")
        self._dev = ctypes.c_void_p()
        rc = self._lib.rtlsdr_open(ctypes.byref(self._dev), index)
        if rc != 0:
            raise RtlSdrError(
                f"rtlsdr_open failed ({rc}) -- is the dvb_usb_rtl28xxu kernel driver or another SDR app holding it?"
            )

    def close(self):
        if self._dev:
            self._lib.rtlsdr_close(self._dev)
            self._dev = ctypes.c_void_p()

    def configure(self, center_hz, sample_rate, gain_db=None):
        self._lib.rtlsdr_set_sample_rate(self._dev, int(sample_rate))
        self._lib.rtlsdr_set_center_freq(self._dev, int(center_hz))
        if gain_db is None:
            self._lib.rtlsdr_set_tuner_gain_mode(self._dev, 0)
        else:
            self._lib.rtlsdr_set_tuner_gain_mode(self._dev, 1)
            self._lib.rtlsdr_set_tuner_gain(self._dev, self._nearest_gain(gain_db))
        self._lib.rtlsdr_set_agc_mode(self._dev, 0)
        self._lib.rtlsdr_reset_buffer(self._dev)

    def tune(self, center_hz):
        """Retune without touching rate/gain, dropping anything buffered
        from the old frequency."""
        self._lib.rtlsdr_set_center_freq(self._dev, int(center_hz))
        self._lib.rtlsdr_reset_buffer(self._dev)

    def _nearest_gain(self, gain_db):
        n = self._lib.rtlsdr_get_tuner_gains(self._dev, None)
        if n <= 0:
            return int(gain_db * 10)
        gains = (ctypes.c_int * n)()
        self._lib.rtlsdr_get_tuner_gains(self._dev, gains)
        return min(gains, key=lambda g: abs(g - gain_db * 10))

    def read_iq(self, num_samples):
        """num_samples complex64 samples, DC-centred and scaled to +-1."""
        buf = (ctypes.c_uint8 * (num_samples * 2))()
        n_read = ctypes.c_int()
        rc = self._lib.rtlsdr_read_sync(self._dev, buf, len(buf), ctypes.byref(n_read))
        if rc != 0:
            raise RtlSdrError(f"rtlsdr_read_sync failed ({rc})")
        raw = np.frombuffer(buf, dtype=np.uint8, count=n_read.value - (n_read.value % 2)).astype(np.float32)
        raw = (raw - 127.5) / 127.5
        return raw[0::2] + 1j * raw[1::2]


class WaterfallWorker(threading.Thread):
    """Streams IQ from the RTL-SDR and emits one averaged power-spectrum
    row (dBFS, numpy float32 array of `fft_size` bins, low->high freq) per
    `row_interval` seconds via on_row(row). on_error(str) is called once if
    the device can't be opened or drops out."""

    def __init__(self, center_hz, sample_rate, on_row, on_error, fft_size=1024, rows_per_sec=15, gain_db=None):
        super().__init__(daemon=True)
        self.center_hz = center_hz
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.rows_per_sec = rows_per_sec
        self.gain_db = gain_db
        self.on_row = on_row
        self.on_error = on_error
        self._stop_requested = threading.Event()

    def stop(self):
        self._stop_requested.set()

    def run(self):
        try:
            sdr = RtlSdr()
        except Exception as exc:
            self.on_error(str(exc))
            return
        try:
            sdr.configure(self.center_hz, self.sample_rate, self.gain_db)
            window = np.hanning(self.fft_size).astype(np.float32)
            norm = 10 * np.log10(np.sum(window) ** 2)
            # Read a whole row's worth of samples per call and average every
            # FFT in it -- max-hold would show bursts better but averaging
            # keeps the noise floor smooth, and LoRa chirps are long enough
            # (tens to hundreds of ms) to stand out regardless.
            per_row = int(self.sample_rate / self.rows_per_sec)
            ffts_per_row = max(1, per_row // self.fft_size)
            read_len = ffts_per_row * self.fft_size
            read_len -= read_len % 512  # librtlsdr wants multiples of 512 bytes
            ffts_per_row = read_len // self.fft_size
            while not self._stop_requested.is_set():
                iq = sdr.read_iq(read_len)
                if len(iq) < self.fft_size:
                    continue
                iq = iq - iq.mean()
                blocks = iq[: (len(iq) // self.fft_size) * self.fft_size].reshape(-1, self.fft_size) * window
                spectrum = np.fft.fftshift(np.abs(np.fft.fft(blocks, axis=1)) ** 2, axes=1)
                power = spectrum.mean(axis=0)
                row = 10 * np.log10(power + 1e-12) - norm
                self.on_row(row.astype(np.float32))
        except Exception as exc:
            self.on_error(str(exc))
        finally:
            sdr.close()


def _power_db(iq, window, norm):
    """Averaged, fftshifted power spectrum (dBFS) of iq in len(window) blocks."""
    fft_size = len(window)
    iq = iq - iq.mean()
    blocks = iq[: (len(iq) // fft_size) * fft_size].reshape(-1, fft_size) * window
    spectrum = np.fft.fftshift(np.abs(np.fft.fft(blocks, axis=1)) ** 2, axes=1)
    return 10 * np.log10(spectrum.mean(axis=0) + 1e-12) - norm


def plan_sweep(lo_hz, hi_hz, sample_rate=2_400_000, fft_size=1024, keep_bins=768):
    """How to cover lo_hz..hi_hz with an RTL-SDR whose single view is only
    sample_rate wide. Returns (centers, center_hz, span_hz, keep_bins):
    the tuning frequencies to visit each row, and what the stitched row
    covers. A range that fits in one view uses one tuning and every bin;
    a wider one is split into segments, each contributing only its middle
    keep_bins (the dongle's filter rolls off toward the edges)."""
    mid = (lo_hz + hi_hz) / 2
    seg_hz = sample_rate * keep_bins / fft_size
    if hi_hz - lo_hz <= seg_hz:
        return [mid], mid, sample_rate, fft_size
    n = math.ceil((hi_hz - lo_hz) / seg_hz)
    span = n * seg_hz
    start = mid - span / 2
    return [start + seg_hz * (k + 0.5) for k in range(n)], mid, span, keep_bins


class SweepWaterfallWorker(threading.Thread):
    """Like WaterfallWorker, but covers an arbitrary lo_hz..hi_hz range --
    e.g. every frequency the LoRa sniffer hops across -- by retuning across
    it and stitching the segments into one row (see plan_sweep). A range
    that fits in one view never retunes. .center_hz / .span_hz say what a
    row covers, so the canvas can be configured before start()."""

    # Retuning the R82xx tuner itself takes ~33 ms, so keep the reads short:
    # ~4-5 rows/s across a 4-segment sweep, close to the sniffer RSSI rate.
    SETTLE_SAMPLES = 16_384  # dropped after each retune while the PLL settles
    SEGMENT_SAMPLES = 32_768  # measured per segment (~14 ms at 2.4 MS/s)

    def __init__(self, lo_hz, hi_hz, on_row, on_error, sample_rate=2_400_000, fft_size=1024,
                 rows_per_sec=15, gain_db=None):
        super().__init__(daemon=True)
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.rows_per_sec = rows_per_sec
        self.gain_db = gain_db
        self.on_row = on_row
        self.on_error = on_error
        self.centers, self.center_hz, self.span_hz, self.keep_bins = plan_sweep(
            lo_hz, hi_hz, sample_rate, fft_size
        )
        self._stop_requested = threading.Event()

    def stop(self):
        self._stop_requested.set()

    def run(self):
        try:
            sdr = RtlSdr()
        except Exception as exc:
            self.on_error(str(exc))
            return
        try:
            sdr.configure(self.centers[0], self.sample_rate, self.gain_db)
            window = np.hanning(self.fft_size).astype(np.float32)
            norm = 10 * np.log10(np.sum(window) ** 2)
            k0 = (self.fft_size - self.keep_bins) // 2
            k1 = k0 + self.keep_bins
            if len(self.centers) == 1:
                read_len = int(self.sample_rate / self.rows_per_sec)
            else:
                read_len = self.SEGMENT_SAMPLES
            read_len -= read_len % 512  # librtlsdr wants multiples of 512 bytes
            while not self._stop_requested.is_set():
                parts = []
                for center in self.centers:
                    if len(self.centers) > 1:
                        sdr.tune(center)
                        sdr.read_iq(self.SETTLE_SAMPLES)
                    iq = sdr.read_iq(read_len)
                    if len(iq) < self.fft_size:
                        break
                    parts.append(_power_db(iq, window, norm)[k0:k1])
                    if self._stop_requested.is_set():
                        break
                if len(parts) == len(self.centers):
                    self.on_row(np.concatenate(parts).astype(np.float32))
        except Exception as exc:
            self.on_error(str(exc))
        finally:
            sdr.close()
