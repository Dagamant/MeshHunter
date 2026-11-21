"""RF sensor decoding on the RTL-SDR, through rtl_433.

The RF band's "Sensors" mode hands the dongle to rtl_433 (an external
program: https://github.com/merbanan/rtl_433), which decodes ~270 kinds of
ISM-band devices -- weather stations, utility meters, TPMS, doorbells,
security sensors. Decoding doesn't depend on frequency; what matters is
where the dongle is tuned, so Rtl433Worker hops between the centres where
those devices actually transmit, running one rtl_433 process per hop.
(Our own loop rather than rtl_433's -H, since its sample rate is global and
we want to know which frequency is live at any moment.)

In 902-928 MHz (the default):
  912.6 MHz  Itron ERT meters (SCM, SCM+, IDM, NetIDM), Neptune R900 --
             these hop 910-920 MHz; 912.6 is rtlamr's usual centre
  915.0 MHz  US 915 weather/home sensors: Fine Offset / Ecowitt / Ambient
             Weather, LaCrosse TX141 / IT+ / View, F007TH, pool remotes
  916.6 MHz  Badger ORION water meters (916.45), Honeywell ActivLink (916.8)
Optional extras outside 33 cm (the 915 MHz antenna hears these less well):
  315 MHz US TPMS and keyfobs; 345 MHz Honeywell 5800 / 2Gig security;
  433.92 MHz AcuRite, Oregon, Bresser, LaCrosse 433, ThermoPro...

Not decodable this way: Davis Vantage (FHSS), Z-Wave, LoRaWAN, Sensus
FlexNet and Landis+Gyr Gridstream.

SensorLog keeps one row per device (model + id + channel) in
rf_sensors.csv, with where it was heard best, and appends every raw decode
to rf_sensors.jsonl.
"""

import csv
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone

from .paths import RF_SENSORS_CSV_PATH, RF_SENSORS_RAW_PATH

# (centre Hz, sample rate, what's there)
HOPS_33CM = [
    (912_600_000, 2_400_000, "utility meters (Itron ERT, Neptune R900)"),
    (915_000_000, 2_400_000, "915 MHz weather/home sensors (Ecowitt, Ambient, LaCrosse)"),
    (916_600_000, 2_400_000, "Badger ORION meters, Honeywell ActivLink"),
]
EXTRA_HOPS = {
    "315": (315_000_000, 1_024_000, "315 MHz: US TPMS, keyfobs, garage remotes"),
    "345": (345_000_000, 1_024_000, "345 MHz: Honeywell 5800 / 2Gig security sensors"),
    "433": (433_920_000, 1_024_000, "433.92 MHz: AcuRite, Oregon, Bresser, LaCrosse 433, ThermoPro"),
}
DEFAULT_DWELL_S = 20
BUSY_RETRY_S = 10

INSTALL_HINT = ("rtl_433 isn't installed. Install it (Debian/Ubuntu/Raspberry Pi OS: "
                "sudo apt install rtl-433 · macOS: brew install rtl_433 · Windows: "
                "the release zip from github.com/merbanan/rtl_433), or set rtl_433_path in config.json.")

CATEGORIES = (
    ("Meter", re.compile(r"ERT|SCM|IDM|R900|Neptune|Badger|ORION|Itron|Kamstrup|M-Bus|Meter", re.I)),
    ("TPMS", re.compile(r"TPMS|Schrader|Toyota|Ford|Citroen|Renault|Hyundai|Kia|PMV|Jansite|BMW|Porsche|"
                        r"Abarth|Elantra|Steelmate|TyreGuard|EezTire|Nissan|GM-", re.I)),
    ("Security", re.compile(r"Honeywell(?!-ActivLink)|2Gig|DSC|Interlogix|GE-|Jasco|Vivint|Ademco|"
                            r"Door|Window|Motion|Contact|Smoke|Alarm|Visonic|SimpliSafe", re.I)),
    ("Doorbell", re.compile(r"ActivLink|Doorbell|Chime", re.I)),
    ("Remote", re.compile(r"Remote|Keyfob|KeeLoq|HCS\d|Garage|Gate|Button|Switch|Intertechno|Nexa|Proove", re.I)),
    ("Leak", re.compile(r"Leak|Water-?Sensor|WH55|H5054", re.I)),
    ("Weather", re.compile(r"Fineoffset|Fine-?Offset|Ecowitt|Ambient|LaCrosse|Acurite|Oregon|Bresser|TFA|"
                           r"Nexus|Rubicson|Cotech|Emax|Misol|Alecto|Ventus|ThermoPro|Inkbird|Govee|"
                           r"Weather|WS\d|WH\d|TX\d|Rain|Wind|Thermo|Hygro|Temp|Soil|Lightning|Pool|Grill", re.I)),
)

# Fields that describe the reception or the device, not a reading.
META_FIELDS = {
    "time", "model", "id", "channel", "subtype", "type", "protocol", "mic", "mod", "freq", "freq1", "freq2",
    "rssi", "snr", "noise", "hop_hz", "lat", "lon", "raw_msg", "message_type", "ert_type", "rbit", "sequence",
    "flags", "test",
}

UNITS = (
    ("_C", "°C"), ("_F", "°F"), ("_kPa", " kPa"), ("_PSI", " psi"), ("_bar", " bar"), ("_hPa", " hPa"),
    ("_inHg", " inHg"), ("_m_s", " m/s"), ("_km_h", " km/h"), ("_mi_h", " mph"), ("_mm", " mm"),
    ("_in", " in"), ("_deg", "°"), ("_lux", " lx"), ("_ppm", " ppm"), ("_V", " V"), ("_m", " m"),
    ("_km", " km"), ("_kWh", " kWh"), ("_W", " W"), ("_ug_m3", " µg/m³"),
)

CSV_FIELDS = ["key", "category", "model", "id", "channel", "first_seen", "last_seen", "count",
              "best_rssi", "last_rssi", "last_snr", "freq_mhz", "lat", "lon", "readings", "last_json"]


def hop_plan(extras=()):
    """[(centre Hz, sample rate, description)]: the 33 cm hops plus any
    chosen extras ("315", "345", "433"), in that order."""
    return list(HOPS_33CM) + [EXTRA_HOPS[k] for k in ("315", "345", "433") if k in set(extras or ())]


def classify(model):
    model = model or ""
    for name, pattern in CATEGORIES:
        if pattern.search(model):
            return name
    return "Other"


def device_key(event):
    parts = [str(event.get("model", "?")), str(event.get("id", "")), str(event.get("channel", ""))]
    return "|".join(parts)


def _fmt_value(value):
    if isinstance(value, float):
        return f"{value:.1f}" if abs(value) < 1000 else f"{value:.0f}"
    return str(value)


def readings(event):
    """The event's readings as short text: "21.4°C · 56% · bat ok · ...". """
    out = []
    for key, value in event.items():
        if key in META_FIELDS or value is None or isinstance(value, (dict, list)):
            continue
        if key == "battery_ok":
            out.append("bat ok" if value in (1, True, "1") else "bat LOW")
            continue
        if key == "humidity":
            out.append(f"{_fmt_value(value)}%")
            continue
        match = next(((suffix, unit) for suffix, unit in UNITS if key.endswith(suffix)), None)
        if match is not None:
            name = key[: -len(match[0])]
            label = "" if name in ("temperature", "pressure") else name.replace("_", " ") + " "
            out.append(f"{label}{_fmt_value(value)}{match[1]}")
        else:
            out.append(f"{key.replace('_', ' ')} {_fmt_value(value)}")
    return " · ".join(out)


def rtl_433_path(config=None):
    configured = (config or {}).get("rtl_433_path") or ""
    if configured or shutil.which("rtl_433"):
        return configured or shutil.which("rtl_433")
    # Homebrew's bin isn't on PATH for apps started outside a login shell.
    return next((p for p in ("/opt/homebrew/bin/rtl_433", "/usr/local/bin/rtl_433") if os.access(p, os.X_OK)), "")


def rtl_433_command(exe, freq_hz, sample_rate, dwell_s, gain_db=None):
    cmd = [exe, "-f", str(int(freq_hz)), "-s", str(int(sample_rate)), "-T", str(int(dwell_s)),
           "-F", "json", "-M", "level", "-M", "time:iso:utc", "-M", "protocol"]
    if gain_db is not None:
        cmd += ["-g", f"{gain_db:g}"]
    return cmd


class Rtl433Worker(threading.Thread):
    """Hops rtl_433 across hop_plan(extras), dwell_s per hop. Callbacks run
    on this thread: on_event(dict) per decode (with hop_hz, time and the
    GPS fix added), on_hop(freq_hz, sample_rate, dwell_s, description) as
    each hop starts, on_error(text) for problems (it keeps retrying unless
    rtl_433 is missing)."""

    def __init__(self, on_event, on_hop, on_error, *, extras=(), dwell_s=DEFAULT_DWELL_S, gain_db=None,
                 gps_state=None, exe=None):
        super().__init__(daemon=True, name="rtl_433")
        self.on_event, self.on_hop, self.on_error = on_event, on_hop, on_error
        self.extras = list(extras)  # read at each hop, so changes apply at the next one
        self.dwell_s = max(5, int(dwell_s or DEFAULT_DWELL_S))
        self.gain_db = gain_db
        self.gps_state = gps_state
        self.exe = exe if exe is not None else rtl_433_path()
        self._stop = threading.Event()
        self._proc = None
        self._lock = threading.Lock()

    def stop(self):
        self._stop.set()
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def join(self, timeout=None):
        super().join(timeout)
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def run(self):
        if not self.exe:
            self.on_error(INSTALL_HINT)
            return
        index = 0
        while not self._stop.is_set():
            plan = hop_plan(self.extras)
            freq, rate, what = plan[index % len(plan)]
            index += 1
            # A single hop just stays put: run it in long chunks.
            dwell = self.dwell_s if len(plan) > 1 else max(self.dwell_s, 300)
            self.on_hop(freq, rate, dwell, what)
            busy = self._run_hop(freq, rate, dwell)
            if busy:
                self.on_error(f"RTL-SDR busy ({busy}); retrying in {BUSY_RETRY_S} s")
                if self._stop.wait(BUSY_RETRY_S):
                    break

    def _run_hop(self, freq, rate, dwell):
        """Runs rtl_433 for one hop. Returns an error string if the dongle
        couldn't be opened, else ""."""
        cmd = rtl_433_command(self.exe, freq, rate, dwell, self.gain_db)
        try:
            # UTF-8 explicitly: Windows would otherwise decode with the ANSI
            # code page. No console window for it there either.
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
                                    encoding="utf-8", errors="replace",
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self.on_error(f"couldn't start rtl_433: {exc}")
            self._stop.wait(BUSY_RETRY_S)
            return ""
        with self._lock:
            self._proc = proc
        stderr_lines = []
        drain = threading.Thread(target=lambda: stderr_lines.extend(proc.stderr), daemon=True)
        drain.start()
        decoded = False
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or "model" not in event:
                continue
            decoded = True
            event["hop_hz"] = freq
            event.setdefault("time", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
            fix = getattr(self.gps_state, "fix", None)
            if fix:
                event["lat"], event["lon"] = fix
            self.on_event(event)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        drain.join(timeout=1)
        with self._lock:
            self._proc = None
        if self._stop.is_set() or decoded:
            return ""
        err = "".join(stderr_lines)
        if "usb_claim_interface" in err or "Unable to open" in err or "Failed to open" in err:
            return "usb_claim_interface" if "usb_claim" in err else "can't open the device"
        if proc.returncode not in (0, None) and not stderr_lines:
            self.on_error(f"rtl_433 exited with code {proc.returncode}")
        return ""


class SensorLog:
    """One row per device: what it is, when and how well it was heard, its
    latest readings and where the signal was strongest. Persists to
    rf_sensors.csv (rewritten at most every SAVE_EVERY_S) and appends
    every raw decode to rf_sensors.jsonl."""

    SAVE_EVERY_S = 10

    def __init__(self, csv_path=RF_SENSORS_CSV_PATH, raw_path=RF_SENSORS_RAW_PATH):
        self.csv_path = csv_path
        self.raw_path = raw_path
        self.rows = {}
        self.session_keys = set()  # devices heard this session
        self._dirty = False
        self._saved_at = 0.0
        self.load()

    def load(self):
        self.rows = {}
        try:
            with self.csv_path.open(newline="", encoding="utf-8", errors="replace") as f:
                for row in csv.DictReader(f):
                    if row.get("key"):
                        self.rows[row["key"]] = row
        except (OSError, csv.Error):
            pass

    def add(self, event):
        """Record one decode; returns its (updated) row."""
        key = device_key(event)
        now = event.get("time") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        rssi = event.get("rssi")
        freq = event.get("freq") or (event.get("hop_hz") or 0) / 1e6
        row = self.rows.get(key)
        if row is None:
            row = {f: "" for f in CSV_FIELDS}
            row.update(key=key, model=str(event.get("model", "")), id=str(event.get("id", "")),
                       channel=str(event.get("channel", "")), first_seen=now, count="0")
            self.rows[key] = row
        row["category"] = classify(row["model"])
        row["last_seen"] = now
        row["count"] = str(int(row.get("count") or 0) + 1)
        row["last_rssi"] = "" if rssi is None else f"{rssi:.1f}"
        row["last_snr"] = "" if event.get("snr") is None else f"{event['snr']:.1f}"
        row["freq_mhz"] = f"{freq:.3f}" if freq else row.get("freq_mhz", "")
        text = readings(event)
        if text:
            row["readings"] = text
        row["last_json"] = json.dumps(event, separators=(",", ":"))
        best = row.get("best_rssi")
        if rssi is not None and (best in ("", None) or rssi > float(best)):
            row["best_rssi"] = f"{rssi:.1f}"
            if event.get("lat") is not None:
                row["lat"], row["lon"] = f"{event['lat']:.6f}", f"{event['lon']:.6f}"
        elif not row.get("lat") and event.get("lat") is not None:
            row["lat"], row["lon"] = f"{event['lat']:.6f}", f"{event['lon']:.6f}"
        self.session_keys.add(key)
        self._dirty = True
        self._append_raw(event)
        if time.monotonic() - self._saved_at >= self.SAVE_EVERY_S:
            self.save()
        return row

    def _append_raw(self, event):
        try:
            self.raw_path.parent.mkdir(parents=True, exist_ok=True)
            with self.raw_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def save(self):
        self._saved_at = time.monotonic()
        if not self._dirty:
            return
        try:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.csv_path.with_suffix(".csv.tmp")
            with tmp.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
                writer.writeheader()
                for row in sorted(self.rows.values(), key=lambda r: r.get("last_seen", ""), reverse=True):
                    writer.writerow(row)
            tmp.replace(self.csv_path)
            self._dirty = False
        except OSError:
            pass
